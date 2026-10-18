from __future__ import annotations

import fcntl
import json
import math
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from route_storage import _parse_ts, atomic_write


def load_quota_policy(table: dict[str, Any], table_path: Path) -> dict[str, Any]:
    path = table_path.with_name("quota-swap.json")
    if not path.is_file():
        path = Path(__file__).resolve().parent.parent / "config" / "coding-agents" / "quota-swap.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    for name, standard in policy.get("standards", {}).items():
        table.setdefault("standards", {}).setdefault(name, standard)
    table.setdefault("quota_swap", policy["quota_swap"])
    return table


def quota_swap_state(table: dict[str, Any], fetch_pools: Callable,
                     api_request_logs: Callable, append_ledger: Callable) -> dict[str, Any]:
    policy = (table.get("policy") or {}).get("reserve") or {}
    reserve = float(policy.get("headroom_min_percent", 15)) / 100
    document, _ = fetch_pools()
    pool = next((pool for pool in document.get("pools", [])
                 if pool.get("id") == policy.get("pool", "anthropic-general")), {})
    percent = pool.get("aggregateRemainingPercent")
    remaining = (float(percent) / 100 if isinstance(percent, (int, float))
                 and not isinstance(percent, bool) and math.isfinite(percent) else None)
    now = datetime.now(timezone.utc)
    errors = api_request_logs(now - timedelta(minutes=15))
    stamps = []
    for row in errors:
        if not (row.get("provider") == "anthropic" or str(row.get("model", "")).startswith("claude-")):
            continue
        code = str(row.get("errorCode") or row.get("error_code")
                   or row.get("upstreamErrorCode") or row.get("upstream_error_code") or "").lower()
        upstream = row.get("upstreamStatusCode", row.get("upstream_status_code"))
        if str(upstream) != "429" and code not in (
                "429", "rate_limit_exceeded", "rate_limit_error", "usage_limit_reached",
                "usage_limit_exceeded", "quota_exceeded", "rate_limited"):
            continue
        stamp = _parse_ts(row.get("requestedAt") or row.get("requested_at"))
        if stamp and stamp.tzinfo and 0 <= (now - stamp).total_seconds() <= 900:
            stamps.append(stamp)
    path = Path(os.environ.get("ROUTE_QUOTA_MODE_STATE") or
                Path.home() / ".claude" / "route-quota-mode.json").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            state = {}
        previous_error = _parse_ts(state.get("last_limit_error_at"))
        if previous_error and previous_error.tzinfo:
            stamps.append(previous_error)
        latest = max(stamps) if stamps else None
        age = (now - latest).total_seconds() if latest else math.inf
        mode = state.get("mode", "anthropic")
        cause = None
        if mode == "anthropic":
            if 0 <= age <= 600:
                cause = "Anthropic usage-limit error within 10 minutes"
            elif remaining is not None and remaining < reserve:
                cause = "Anthropic remaining below reserve"
            if cause:
                mode = "codex-swap"
        elif remaining is not None and remaining > reserve + 0.10 and age > 900:
            mode = "anthropic"
            cause = "Anthropic remaining above reserve + 10 points; no limit error for 15 minutes"
        if cause:
            state.update(last_flip_at=now.isoformat(), last_flip_cause=cause)
            append_ledger({"event": "route_quota_mode_flip", "direction": mode,
                           "remaining_fraction": remaining, "cause": cause})
        state.update(mode=mode, remaining_fraction=remaining, reserve_fraction=reserve,
                     last_limit_error_at=latest.isoformat() if latest else None)
        state.setdefault("last_flip_at", None)
        state.setdefault("last_flip_cause", None)
        atomic_write(path, json.dumps(state, sort_keys=True))
    return state


def swapped_standard(table: dict[str, Any], class_name: str, standard: dict[str, Any],
                     state: dict[str, Any]) -> dict[str, Any]:
    if state["mode"] != "codex-swap":
        return standard
    override = (table.get("quota_swap") or {}).get(class_name)
    return {**standard, **override} if isinstance(override, dict) else standard


def alias_exhausted(pool: dict[str, Any], body: Any) -> bool:
    rows = body.get("accounts") if isinstance(body, dict) else body
    relevant = [row for row in rows if isinstance(row, dict)
                and row.get("provider") == "anthropic"
                and row.get("status") in ("active", "rate_limited", "quota_exceeded")
                ] if isinstance(rows, list) else []
    eligible = pool.get("eligibleAccounts")
    return (bool(relevant)
            and all(row.get("status") in ("rate_limited", "quota_exceeded") for row in relevant)
            and isinstance(eligible, int) and not isinstance(eligible, bool)
            and eligible == 0 and pool.get("accounts", 0) > 0
            and pool.get("source") != "accounts_fallback")


def degrade_alias(result: dict[str, Any], table: dict[str, Any], served: Any,
                  fetch_pools: Callable, pool_for_model: Callable, http_get: Callable,
                  resolve_alias: Callable, append_ledger: Callable) -> None:
    name = result["alias"]
    pools, _ = fetch_pools()
    pool_id = pool_for_model(table, name)
    pool = next((row for row in pools.get("pools", []) if row.get("id") == pool_id), {})
    accounts = http_get("/api/accounts")
    if not alias_exhausted(pool, accounts.body if accounts.json_ok else None):
        return
    replacement, _ = resolve_alias(table, "sol-latest", served)
    if replacement:
        result.update(model=replacement, degraded_from=name, resolved_alias="sol-latest",
                      pool=pool_id, reason="no eligible Anthropic accounts")
        append_ledger({"event": "model_degraded", "source": "route resolve", **result})
