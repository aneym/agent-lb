from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from route_anthropic_models import http_get, ledger_now_iso, percent, request, retired_patterns, served_models  # pyrefly: ignore [missing-import]
from route_quota import degrade_alias, quota_swap_state, swapped_standard  # pyrefly: ignore [missing-import]
from route_reserve_state import dispatch_standard, pool_for_model, pools_by_id, reservation_capacity  # pyrefly: ignore [missing-import]
from route_resolve_one import resolve_alias  # pyrefly: ignore [missing-import]
from route_seat import fetch_pools, pace_number, pace_timestamp, pool_remaining  # pyrefly: ignore [missing-import]
from route_seat_states import api_request_logs  # pyrefly: ignore [missing-import]
from route_storage import atomic_write  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_SETUP,
    EXIT_UNROUTABLE,
    READ_TIMEOUT_SECONDS,
    env_path,
    ledger_path,
    load_table,
    now_iso,
    parse_ts,
)


def load_pool_history(now: datetime) -> list[dict]:
    path = env_path("ROUTE_POOL_HISTORY", Path.home() / ".claude" / "route-pool-history.jsonl")
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            stamp = pace_timestamp(row.get("ts"))
            if (
                stamp is not None
                and now - timedelta(hours=72) <= stamp <= now
                and isinstance(row.get("pool"), str)
                and pace_number(row.get("remaining")) is not None
            ):
                rows.append(row)
    except (OSError, UnicodeError):
        pass
    return rows


def record_pool_history(document: dict, now: datetime) -> None:
    rows = load_pool_history(now)
    path = env_path("ROUTE_POOL_HISTORY", Path.home() / ".claude" / "route-pool-history.jsonl")
    try:
        original_count = len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeError):
        original_count = 0
    changed = original_count != len(rows)
    newest: dict[str, datetime] = {}
    for row in rows:
        stamp = pace_timestamp(row["ts"])
        if row["pool"] not in newest or stamp > newest[row["pool"]]:
            newest[row["pool"]] = stamp
    for pool_id, pool in pools_by_id(document).items():
        remaining = pool_remaining(pool)
        if remaining is None or (pool_id in newest and now - newest[pool_id] < timedelta(minutes=15)):
            continue
        rows.append({"ts": now.isoformat(), "pool": pool_id, "remaining": remaining})
        changed = True
    if not changed:
        return
    try:
        atomic_write(path, "".join(json.dumps(row) + "\n" for row in rows))
    except OSError:
        pass


def admission_state(pool: dict, history: list[dict], policy: dict, now: datetime) -> dict:
    result = dict(
        state="unknown",
        remaining=None,
        hours_left=None,
        burn_per_hour=None,
        empties_in_h=None,
        leftover=None,
        basis="none",
        next_refill_h=None,
    )
    result["next_refill_h"] = (pool.get("pace") or {}).get("next_refill_h")
    if pool.get("status") == "exhausted":
        result["state"] = "exhausted"
        return result
    monthly = pool.get("windowLabel") == "month"
    remaining = pool_remaining(pool)
    reset = pace_timestamp(pool.get("cycleResetAt" if monthly else "weeklyResetAt")) or pace_timestamp(
        pool.get("resetAt")
    )
    hours_left = (reset - now).total_seconds() / 3600 if reset is not None else None
    if hours_left is not None and hours_left <= 0:
        hours_left = None
    burn = pace_number(pool.get("burn24hPercent"))
    burn = max(0.0, burn / 24) if burn is not None else None
    if burn is None and remaining is not None:
        samples = []
        for row in history:
            if not isinstance(row, dict) or row.get("pool") != pool.get("id"):
                continue
            stamp = pace_timestamp(row.get("ts"))
            value = pace_number(row.get("remaining"))
            if stamp is not None and stamp <= now and value is not None:
                samples.append((stamp, value))
        samples.sort()
        baseline = next(
            (sample for sample in samples if now - timedelta(hours=28) <= sample[0] <= now - timedelta(hours=20)), None
        )
        if baseline is not None:
            later = [sample for sample in samples if sample[0] > baseline[0]]
            values = [baseline[1], *(sample[1] for sample in later), remaining]
            if not any(current > previous + 5 for previous, current in zip(values, values[1:])):
                elapsed = (now - baseline[0]).total_seconds() / 3600
                burn = max(0.0, (baseline[1] - remaining) / elapsed)
    result.update(remaining=remaining, hours_left=hours_left, burn_per_hour=burn)  # pyrefly: ignore [bad-argument-type]
    pace_policy = policy.get("pace") if isinstance(policy, dict) else None
    pace_policy = pace_policy if isinstance(pace_policy, dict) else {}
    thresholds = {}
    for key, default in (("rich_leftover", 15), ("behind_lt", -10), ("ahead_gt", 15)):
        value = pace_number(pace_policy.get(key))
        thresholds[key] = default if value is None else value
    if burn is not None and remaining is not None and hours_left is not None:
        empties = remaining / burn if burn != 0 else None
        leftover = remaining - burn * hours_left
        rich_leftover = thresholds["rich_leftover"]
        state = (
            "low"
            if empties is not None and empties < hours_left
            else ("rich" if leftover >= rich_leftover else "on_pace")
        )
        result.update(state=state, empties_in_h=empties, leftover=leftover, basis="burn_24h")
    elif burn is None:
        pace = pace_number(pool.get("monthlyPacePercent" if monthly else "admissionPacePercent"))
        if pace is not None:
            state = (
                "low" if pace < thresholds["behind_lt"] else ("rich" if pace > thresholds["ahead_gt"] else "on_pace")
            )
            result.update(state=state, basis="pace")
    return result


def command_pools(args: argparse.Namespace) -> int:
    document, error = fetch_pools(refills=True)
    if document is None:
        print(f"route: {error}", file=sys.stderr)
        return EXIT_DEGRADED
    now = datetime.now(timezone.utc)
    record_pool_history(document, now)
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"pools at {document.get('generatedAt')} (source: {document.get('source')})")
    header = f"{'pool':<20} {'status':<10} {'pace':<12} {'headroom':>9} {'aggregate':>10} {'usable':>8}  reset"
    print(header)
    print("-" * len(header))
    for pool in document.get("pools", []):
        usable = f"{pool.get('eligibleAccounts')}/{pool.get('accounts')}"
        pace_label = {
            "critical": "critical",
            "behind": "behind",
            "ahead": "ahead",
            "rich": "running rich",
            "on_pace": "on pace",
            "exhausted": "empty",
            "unknown": "unknown",
        }.get((pool.get("pace") or {}).get("tier", "unknown"), "unknown")
        print(
            f"{pool.get('id', '?'):<20} {pool.get('unavailableReason') or pool.get('status', '?'):<10} "
            f"{pace_label:<12} "
            f"{percent(pool.get('headroomPercent')):>9} {percent(pool.get('aggregateRemainingPercent')):>10} "
            f"{usable:>8}  {pool.get('resetAt') or '-'}"
        )
        if "refills" in pool:
            refills = []
            for refill in pool["refills"]:
                stamp = parse_ts(refill["at"])
                refills.append(f"{stamp:%m-%d %H:%MZ} +{refill['accounts']} accounts")
            print("  refills: " + (", ".join(refills) or "none"))
    return EXIT_OK


def append_ledger(record: dict[str, Any]) -> None:

    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": ledger_now_iso(), **record, "cwd": os.getcwd()}, sort_keys=True) + "\n")
    except OSError:
        pass


def record_explicit_model(model: str, source: str) -> None:

    append_ledger({"event": "explicit_model", "model": model, "source": source})


def command_quota_mode(args: argparse.Namespace) -> int:
    state = quota_swap_state(load_table(), fetch_pools, api_request_logs, append_ledger)
    print(
        json.dumps(state, sort_keys=True)
        if args.json
        else f"{state['mode']} remaining={state['remaining_fraction']} "
        f"last_flip={state['last_flip_at']} cause={state['last_flip_cause']}"
    )
    return EXIT_OK


def command_resolve(args: argparse.Namespace) -> int:
    table = load_table()
    served, source = served_models()
    reasons = []
    unknown = []
    for name in args.aliases:
        if name in (table.get("standards") or {}):
            standard = swapped_standard(
                table,
                name,
                dispatch_standard(table, name, None),
                quota_swap_state(table, fetch_pools, api_request_logs, append_ledger),
            )
            model, reason = resolve_alias(table, standard["model"], served, standard["seat"])
            if model:
                result = {
                    "alias": name,
                    "model": model,
                    "resolved_alias": standard["model"],
                    "seat": standard["seat"],
                    "effort": standard.get("effort"),
                    "reason": reason,
                }
                print(json.dumps(result, sort_keys=True) if args.json else model)
                return EXIT_OK
            reasons.append(reason)
            continue
        model, reason = resolve_alias(table, name, served)
        if not model and reason == f"{name} is retired":
            model = name
            record_explicit_model(name, "route resolve")
        if model:
            result = {"alias": name, "model": model, "reason": reason}
            if name in ("opus-latest", "sonnet-latest"):
                degrade_alias(
                    result, table, served, fetch_pools, pool_for_model, http_get, resolve_alias, append_ledger
                )
            print(json.dumps(result, sort_keys=True) if args.json else result["model"])
            return EXIT_OK
        if reason == f"unknown alias {name}":
            unknown.append(name)
        reasons.append(f"{name}: {reason}")
    if unknown:
        print(f"route: unknown alias {', '.join(unknown)}", file=sys.stderr)
        return EXIT_SETUP
    print(f"route: nothing resolvable ({'; '.join(reasons)}; models from {source})", file=sys.stderr)
    return EXIT_UNROUTABLE


def command_models(args: argparse.Namespace) -> int:
    table = load_table()
    served, source = served_models()
    aliases = table.get("aliases") if isinstance(table.get("aliases"), dict) else {}
    rows = []
    for name in aliases:
        model, reason = resolve_alias(table, name, served)
        rows.append({"alias": name, "model": model, "reason": reason})
    if args.json:
        print(json.dumps({"source": source, "aliases": rows, "retired": list(retired_patterns(table))}, indent=2))
        return EXIT_OK
    print(f"models from {source}")
    for row in rows:
        print(f"{row['alias']:<15} {row['model'] or '-':<18} {row['reason']}")
    print("retired         " + ", ".join(retired_patterns(table)))
    return EXIT_OK


def state_age_seconds(state: dict[str, Any]) -> float | None:
    if not state:
        return None
    stamp = parse_ts(state.get("ts"))
    if stamp is None:
        return None
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def seat_is_up(seats: Any, seat: str) -> bool:

    if not isinstance(seats, dict):
        return True
    record = seats.get(seat)
    if not isinstance(record, dict):
        return True
    if record.get("ok") is not False:
        return True
    hold_until = parse_ts(record.get("hold_until"))
    return hold_until is not None and hold_until <= datetime.now(timezone.utc)


def record_caller_choice(args: argparse.Namespace, result: dict) -> None:

    prefer, effort = args.prefer, args.effort
    if not prefer and not effort:
        return
    if effort:
        result["effort_default"] = result.get("effort")
        result["effort"] = effort
        result["reason"] = f"{result.get('reason')}; effort {effort} by caller"
    result["caller_choice"] = {"prefer": prefer, "effort": effort, "why": args.reason}
    append_ledger(
        {
            "event": "route_override",
            "class": args.task_class,
            "prefer": prefer,
            "effort": effort,
            "why": args.reason,
            "rung": result.get("rung"),
            "seat": result.get("seat"),
            "model": result.get("model"),
            "default": result.get("default"),
            "effort_default": result.get("effort_default"),
        }
    )


def pick_leases() -> dict | None:
    code, snapshot = reservation_request("GET", "", timeout=3.0, fixture=True)
    rows = snapshot.get("live")
    if (
        code == EXIT_OK
        and isinstance(rows, list)
        and all(
            isinstance(row, dict)
            and isinstance(row.get("capacity_key"), str)
            and isinstance(row.get("expires_at"), str)
            for row in rows
        )
    ):
        return snapshot
    print("route: reservation availability unknown; reserve remains the admission gate", file=sys.stderr)
    return None


def lease_unavailable(table: dict, row: dict, pools: dict, leases: dict | None) -> str | None:
    if leases is None:
        return None
    policy = (table.get("policy") or {}).get("concurrency") or {}
    key, capacity = reservation_capacity(row, pools, policy)
    stamp = now_iso()
    live = sum(1 for held in leases["live"] if held.get("capacity_key") == key and held.get("expires_at", "") > stamp)
    if live < capacity:
        return None
    reason = f"lease capacity reserved: {key} ({live}/{capacity}); all candidate capacity is reserved"
    append_ledger(
        {
            "event": "route_pick_lease_skip",
            "seat": row["seat"],
            "capacity_key": key,
            "live": live,
            "capacity": capacity,
            "reason": reason,
        }
    )
    return reason


def reservation_request(
    method: str, path: str, payload: dict | None = None, timeout: float = READ_TIMEOUT_SECONDS, *, fixture: bool = False
) -> tuple[int, dict]:
    response = request(method, "/api/pools/reservations" + path, payload=payload, timeout=timeout, fixture=fixture)
    if response.ok and isinstance(response.body, dict):
        return EXIT_OK, response.body
    if response.status in (404, 410) and path:
        return EXIT_UNROUTABLE, {**(response.body if isinstance(response.body, dict) else {}), "status": "gone"}
    return EXIT_DEGRADED, {
        "status": "error",
        "error": "reservations_unavailable",
        "message": response.error or "invalid reservation response",
    }


def command_heartbeat(args: argparse.Namespace) -> int:
    code, result = reservation_request(
        "POST", f"/{args.reservation_id}/heartbeat", {"ttl_s": args.ttl} if args.ttl is not None else {}
    )
    print(json.dumps(result))
    return code


def command_release(args: argparse.Namespace) -> int:
    code, result = reservation_request("POST", f"/{args.reservation_id}/release", {"outcome": args.outcome})
    print(json.dumps(result))
    return code


def eval_seat_id(decision_id: str) -> str:
    return "eval." + decision_id[len("dcn_") :].lower()
