from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from clients.seat_constants import EXIT_NO_ACCOUNT, EXIT_OK
from clients.seat_outcomes import cooldown_until
from clients.seat_state import fail, iso, now, parse_ts


def usage_since(record: dict[str, Any], since: datetime) -> dict[str, Any]:
    runs = [run for run in record.get("runs") or [] if (parse_ts(run.get("ts")) or since) > since]
    return {
        "runs": len(runs),
        "ok": sum(1 for run in runs if run.get("ok")),
        "wall_s": round(sum(float(run.get("wall_s") or 0) for run in runs), 1),
        "tokens_in": sum(int(run.get("tokens_in") or 0) for run in runs),
        "tokens_out": sum(int(run.get("tokens_out") or 0) for run in runs),
    }


def cursor_pool(model: str) -> str:
    managed = Path.home() / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
    repo = Path(__file__).resolve().parent.parent / "config" / "coding-agents" / "routing-table.json"
    path = Path(os.environ.get("ROUTE_TABLE") or (managed if managed.is_file() else repo)).expanduser()
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
        definitions = table["cli_pools"]["cursor"]["pools"]
        return next(
            pool["id"]
            for pool in definitions
            if isinstance(pool, dict)
            and isinstance(pool.get("id"), str)
            and isinstance(pool.get("models"), list)
            and any(fnmatch.fnmatchcase(model, pattern) for pattern in pool["models"] if isinstance(pattern, str))
        )
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return "cursor-other"


def cooling(record: dict[str, Any], pool: str | None = None) -> datetime | None:
    values = [record.get("cooldown_until")]
    if pool:
        values.append((record.get("cooldowns") or {}).get(pool))
    return next((until for value in values if (until := parse_ts(value)) and until > now()), None)


def mark_limit(record: dict[str, Any], vendor: str, model: str, text: str) -> None:
    if vendor != "cursor":
        record["cooldown_until"] = iso(cooldown_until(text))
        return
    pool = cursor_pool(model)
    match = re.search(r"reset when your monthly cycle ends on (\d{1,2})/(\d{1,2})/(\d{4})", text, re.I)
    try:
        month, day, year = map(int, match.groups())
        until = datetime(year, month, day, tzinfo=timezone.utc)
    except (AttributeError, ValueError):
        until = None
    if until:
        record.setdefault("limits", {})[pool] = {"at": iso(now()), "until": iso(until), "source": "vendor"}
    record.setdefault("cooldowns", {})[pool] = iso(until or cooldown_until(text))


def active_leases(state: dict[str, Any], account_id: str, current: datetime) -> list[dict[str, Any]]:
    return [
        lease
        for lease in state.get("leases", {}).values()
        if lease.get("account") == account_id
        and lease.get("released_at") is None
        and (parse_ts(lease.get("expires_at")) or current) > current
    ]


def order_accounts(
    registry: list[dict[str, Any]], state: dict[str, Any], vendor: str, model: str | None = None
) -> list[dict[str, Any]]:
    window = now() - timedelta(hours=5)
    ready = []
    for account in registry:
        if account.get("vendor") != vendor or account.get("enabled", True) is False:
            continue
        record = state["accounts"].get(account["id"], {})
        pool = cursor_pool(model) if vendor == "cursor" and model else None
        peers = [record]
        if pool and account.get("plan"):
            peers = [
                state["accounts"].get(peer["id"], {})
                for peer in registry
                if peer.get("vendor") == vendor and peer.get("plan") == account["plan"]
            ]
        pool_cooling = pool and any(
            (until := parse_ts((peer.get("cooldowns") or {}).get(pool))) and until > now() for peer in peers
        )
        unavailable = parse_ts((record.get("model_unavailable") or {}).get(model)) if model else None
        if record.get("auth_ok") is False or cooling(record) or pool_cooling or (unavailable and unavailable > now()):
            continue
        leases = [lease for lease in state.get("leases", {}).values() if lease.get("account") == account["id"]]
        recent = usage_since(record, window)["runs"] + len(active_leases(state, account["id"], now()))
        last_use = max([record.get("last_run_at") or ""] + [lease.get("created_at") or "" for lease in leases])
        ready.append((recent, last_use, account))
    ready.sort(key=lambda item: (item[0], item[1]))
    return [account for _, _, account in ready]


def run_envelope(args: argparse.Namespace, model: str | None, run_id: str, error: str, **extra: Any) -> None:
    print(json.dumps({"ok": False, "vendor": args.vendor, "account": None, "model": model, "run_id": run_id,
                      "vendor_session_id": None, "wall_s": 0, "tokens_in": None, "tokens_out": None,
                      "attempts": [], "error": error, **extra, "result": None}, indent=2))


def ready_candidates(args: argparse.Namespace, registry: list[dict[str, Any]], state: dict[str, Any], model: str,
                     run_id: str) -> tuple[list[dict[str, Any]] | None, int]:
    if args.account:
        candidates = [a for a in registry if a["id"] == args.account and a.get("vendor") == args.vendor]
        if not candidates:
            fail(f"no {args.vendor} account {args.account!r} registered", EXIT_NO_ACCOUNT)
    else:
        candidates = order_accounts(registry, state, args.vendor, model)
    if args.vendor == "cursor" and args.account:
        ready = order_accounts(registry, state, args.vendor, model)
        candidates = [account for account in candidates if account in ready]
    if candidates:
        return candidates, EXIT_OK
    misses = [
        record for account_id, record in state["accounts"].items()
        if any(a["id"] == account_id and a.get("vendor") == args.vendor for a in registry)
        and (until := parse_ts((record.get("model_unavailable") or {}).get(model))) and until > now()
        and (not args.account or account_id == args.account)
    ]
    if misses:
        error = (misses[-1].get("model_unavailable_text") or {}).get(model) or f"Cannot use this model: {model}"
        run_envelope(args, model, run_id, error)
        return None, EXIT_NO_ACCOUNT
    fail(f"no {args.vendor} account is ready (see `seat accounts`)", EXIT_NO_ACCOUNT)
