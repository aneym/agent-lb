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
    try:
        threshold = policy.get("headroom_min_percent", 15)
        reserve = float(threshold) / 100
        if isinstance(threshold, bool) or not math.isfinite(reserve):
            raise ValueError("non-finite or boolean threshold")
    except (TypeError, ValueError):
        reserve = .15
        append_ledger({"event": "route_quota_policy_invalid", "field": "headroom_min_percent",
                       "fallback": 15})
    min_eligible = policy.get("min_eligible", 2)
    if not isinstance(min_eligible, (int, float)) or isinstance(min_eligible, bool):
        min_eligible = 2
    document, _ = fetch_pools()
    entries = document.get("pools") if isinstance(document, dict) else None
    pools = {pool["id"]: pool for pool in entries
             if isinstance(pool, dict) and isinstance(pool.get("id"), str)} if isinstance(entries, list) else {}
    pool = pools.get(policy.get("pool", "anthropic-general"), {})
    percent = pool.get("aggregateRemainingPercent")
    remaining = (float(percent) / 100 if isinstance(percent, (int, float))
                 and not isinstance(percent, bool) and math.isfinite(percent) else None)
    eligible = pool.get("eligibleAccounts")
    headroom = pool.get("headroomPercent")
    eligible = eligible if isinstance(eligible, (int, float)) and not isinstance(eligible, bool) and math.isfinite(eligible) else None
    headroom = headroom / 100 if isinstance(headroom, (int, float)) and not isinstance(headroom, bool) and math.isfinite(headroom) else None
    now = datetime.now(timezone.utc)
    errors = api_request_logs(now - timedelta(minutes=15), cache=True)
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
        if not isinstance(state, dict):
            state = {}
        previous_error = _parse_ts(state.get("last_limit_error_at"))
        if previous_error and previous_error.tzinfo:
            stamps.append(previous_error)
        latest = max(stamps) if stamps else None
        age = (now - latest).total_seconds() if latest else math.inf
        mode = state.get("mode", "anthropic")
        if eligible is not None and eligible < min_eligible:
            state["eligible_recovery_pending"] = True
        eligible_ready = not state.get("eligible_recovery_pending", False)
        if not eligible_ready:
            restored = _parse_ts(state.get("eligible_restored_at"))
            if eligible is None or eligible < min_eligible:
                restored = None
            elif not restored or not restored.tzinfo or restored > now:
                restored = now
            state["eligible_restored_at"] = restored.isoformat() if restored else None
            eligible_ready = restored is not None and (now - restored).total_seconds() >= 900
        cause = None
        if mode == "anthropic":
            if 0 <= age <= 600:
                cause = "Anthropic usage-limit error within 10 minutes"
            elif remaining is not None and remaining < reserve:
                cause = "Anthropic remaining below reserve"
            elif eligible is not None and eligible < min_eligible:
                cause = "Anthropic eligible accounts below reserve"
            elif headroom is not None and headroom < reserve:
                cause = "Anthropic headroom below reserve"
            if cause:
                mode = "codex-swap"
        elif (remaining is not None and remaining > reserve + 0.10
              and eligible is not None and eligible >= min_eligible
              and headroom is not None and headroom > reserve + 0.10 and age > 900
              and eligible_ready):
            mode = "anthropic"
            state.update(eligible_recovery_pending=False, eligible_restored_at=None)
            cause = "Anthropic remaining and headroom above reserve + 10 points; eligible accounts restored; no limit error for 15 minutes"
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
