from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import route_heal  # pyrefly: ignore [missing-import]
from route_anthropic_models import STOP_BUDGET_SECONDS, UNDRAINED, defer_discovery, read_state  # pyrefly: ignore [missing-import]
from route_class_candidates import class_candidates, print_pick, release_job_hold  # pyrefly: ignore [missing-import]
from route_dispatch import build_menu, policy_snapshot  # pyrefly: ignore [missing-import]
from route_grok import hands_refusal, hands_seat  # pyrefly: ignore [missing-import]
from route_ladder_walk import command_ladder_pick, ladder_walk  # pyrefly: ignore [missing-import]
from route_pace_state import (  # pyrefly: ignore [missing-import]
    append_ledger,
    pick_leases,
    record_caller_choice,
    reservation_request,
    state_age_seconds,
)
from route_reserve_state import ReserveStopped, pools_by_id, reservation_capacity, reserve_state, stop_reserve  # pyrefly: ignore [missing-import]
from route_resolve_one import ServedModels, ladder_entries, settle_pending  # pyrefly: ignore [missing-import]
from route_seat import fetch_pools  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_UNROUTABLE,
    STATE_MAX_AGE_SECONDS,
    fail,
    load_table,
    parse_ts,
    setup_error,
)


def command_reserve(args: argparse.Namespace, table: dict | None = None) -> int:
    import signal

    table = load_table() if table is None else table
    args.effort = None
    if args.prefer and not args.reason:
        setup_error("missing_reason", "--prefer needs --reason <why>; it goes to the routing ledger", args.json)
    if args.task_class not in (table.get("classes") or {}):
        fail(f"unknown class {args.task_class!r}")
    if ladder_entries(table, args.task_class) is None:
        setup_error("missing_ladder", f"class {args.task_class!r} has no ladder", args.json)
    if args.task_class == "verify" and not args.author_vendor:
        setup_error("unknown_author", "verify requires --author-vendor <maker>", args.json)

    previous = {each: signal.signal(each, stop_reserve) for each in (signal.SIGTERM, signal.SIGINT)}
    try:
        code, result = reserve_walk(args, table)
    except ReserveStopped as stop:
        reason = f"cancelled by signal {signal.Signals(stop.signum).name}"
        status = "cancelled"
        if UNDRAINED:
            reason = f"{reason}; {'; '.join(UNDRAINED)}, released anyway"
            print(f"route: {'; '.join(UNDRAINED)}; releasing the hold anyway", file=sys.stderr)
        left = release_job_hold(args.job, until=stop.at + STOP_BUDGET_SECONDS)
        if left is not None:
            status, reason = "error", f"{reason}; the hold may still be live until its TTL: {left}"
        append_ledger(
            {"event": "route_reserve", "class": args.task_class, "job": args.job, "status": status, "reason": reason}
        )
        print(json.dumps({"status": status, "reason": reason}))
        return 128 + stop.signum
    finally:
        for each, handler in previous.items():
            signal.signal(each, handler)
    record_caller_choice(args, result)
    append_ledger(
        {
            "event": "route_reserve",
            "class": args.task_class,
            "job": args.job,
            **{key: result.get(key) for key in ("status", "rung", "seat", "reservation_id", "reason")},
        }
    )
    print(json.dumps(result))
    return code


def reserve_walk(args: argparse.Namespace, table: dict) -> tuple[int, dict]:
    import socket

    if refused := hands_refusal(table, args.task_class, args.prefer, args.model):
        return EXIT_UNROUTABLE, {"status": "refused", "reason": refused}
    args.prefer = hands_seat(table, args.task_class, args.prefer, args.model)
    state = read_state()
    age = state_age_seconds(state)
    stale = bool(state) and (age is None or age > STATE_MAX_AGE_SECONDS)
    document, _error = fetch_pools(timeout=3.0)
    now = datetime.now(timezone.utc)
    if document is None and isinstance(state.get("pools"), dict):
        document = state["pools"]
    pools = pools_by_id(document)
    policy = (table.get("policy") or {}).get("concurrency") or {}

    defer_discovery()
    skip = list(args.skip)
    released: str | None = None
    while True:
        walk = ladder_walk(
            table,
            args.task_class,
            pools,
            None if stale else state.get("seats"),
            ServedModels(),
            now,
            args.author_vendor,
            skip,
            args.prefer,
            args.model,
        )
        if args.prefer and not any(args.prefer in (row["rung"], row["seat"]) for row in walk["rungs"]):
            setup_error(
                "unknown_rung",
                f"--prefer {args.prefer!r} is not a rung or seat on the {args.task_class} ladder",
                args.json,
            )
        chosen = walk["chosen"]
        if chosen is None:
            reason = (
                released
                or walk["refused"]
                or (
                    next(row.get("reason") for row in walk["rungs"] if args.prefer in (row["rung"], row["seat"]))
                    if args.prefer
                    else f"nothing routable for class {args.task_class!r}"
                )
            )
            return EXIT_UNROUTABLE, {"status": "refused", "reason": reason}
        rows = [chosen] if args.prefer else walk["routable"]
        candidates = []
        for row in rows:
            key, capacity = reservation_capacity(row, pools, policy)
            candidates.append(
                {
                    **{field: row.get(field) for field in ("rung", "seat", "model", "pool")},
                    "capacity_key": key,
                    "capacity": capacity,
                }
            )
        code, result = route_heal.reserve_candidates(
            reservation_request,
            {
                "job": args.job,
                "class": args.task_class,
                "host": os.environ.get("ROUTE_HOST") or socket.gethostname(),
                "ttl_s": args.ttl if args.ttl is not None else policy.get("ttl_s", 120),
                "pinned": bool(args.prefer),
                "candidates": candidates,
            },
        )
        if code == EXIT_OK and result.get("status") == "wait":
            code = 75
        if code != EXIT_OK or result.get("status") != "reserved":
            return code, result
        row = next((row for row in walk["rungs"] if row["rung"] == result.get("rung")), chosen)
        result.update(
            alias=row.get("alias"),
            effort=row.get("effort"),
            audit=row.get("audit"),
            reason=row.get("reason"),
            heartbeat_s=policy.get("heartbeat_s", 30),
        )
        audit = row.get("audit")
        if not row.get("model_pending") and not (isinstance(audit, dict) and audit.get("model_pending")):
            return code, result
        resolved, released = settle_pending(table, row, args.model)
        lost = False
        if resolved is not None:
            beat, body = reservation_request(
                "POST", f"/{result['reservation_id']}/heartbeat", {"model": resolved.get("model", result.get("model"))}
            )
            if beat == EXIT_OK and body.get("status") == "reserved":
                result.update(resolved)
                result["expires_at"] = body.get("expires_at", result.get("expires_at"))
                return code, result
            lost = beat == EXIT_UNROUTABLE

            released = (
                f"{row['rung']} hold was lost while its models were listed"
                if lost
                else f"{row['rung']} hold could not be renewed after its models were listed: "
                f"{body.get('message') or body.get('status')}"
            )
        if not lost:
            for _attempt in range(2):
                _freed, body = reservation_request(
                    "POST", f"/{result['reservation_id']}/release", {"outcome": "failed"}
                )
                if body.get("status") in ("released", "gone"):
                    break
            else:
                return EXIT_DEGRADED, {
                    "status": "error",
                    "reason": f"{released}; and releasing "
                    f"{result['reservation_id']} failed: {body.get('reason') or body.get('status')}",  # pyrefly: ignore [unbound-name]
                }
        append_ledger(
            {
                "event": "route_reserve_released",
                "class": args.task_class,
                "job": args.job,
                "rung": row["rung"],
                "reservation_id": result["reservation_id"],
                "reason": released,
            }
        )
        skip.append(row["rung"])


def command_pick(args: argparse.Namespace, table: dict | None = None) -> int:
    table = load_table() if table is None else table
    for key in ("prefer", "effort", "reason"):
        setattr(args, key, getattr(args, key, None))
    if (args.prefer or args.effort) and not args.reason:
        setup_error(
            "missing_reason", "--prefer and --effort need --reason <why>; it goes to the routing ledger", args.json
        )
    classes = table.get("classes")
    if not isinstance(classes, dict) or args.task_class not in classes:
        known = ", ".join(sorted(classes)) if isinstance(classes, dict) else "none"
        fail(f"unknown class {args.task_class!r}; known classes: {known}")
    if ladder_entries(table, args.task_class) is not None:
        return command_ladder_pick(args, table)
    spec = classes[args.task_class]  # pyrefly: ignore [unsupported-operation]
    if not isinstance(spec, dict):
        fail(f"class {args.task_class!r} is malformed")

    if spec.get("driver"):
        state = read_state()
        document = state.get("pools") if isinstance(state.get("pools"), dict) else None
        result = {
            "reserve": reserve_state(table, pools_by_id(document), args.task_class),
            "class": args.task_class,
            "state_age_s": None,
            "seat": "driver",
            "model": None,
            "pool": None,
            "reason": "driver class: stays on the driver session, no subagent",
            "fallbacks": [],
        }
        print_pick(result, as_json=args.json)
        return EXIT_OK

    cross_vendor = bool(spec.get("cross_vendor"))
    if cross_vendor and not args.author_vendor:
        fail(
            f"class {args.task_class!r} is cross_vendor: pass --author-vendor <vendor> "
            "so the author's vendor is excluded"
        )

    chain = spec.get("chain")
    if not isinstance(chain, list) or not chain:
        fail(f"class {args.task_class!r} has no chain")

    if args.prefer and not any(isinstance(entry, dict) and entry.get("seat") == args.prefer for entry in chain):
        setup_error("unknown_rung", f"--prefer {args.prefer!r} is not a seat on the {args.task_class} chain", args.json)

    state = read_state()
    age = state_age_seconds(state)
    stale = bool(state) and (age is None or age > STATE_MAX_AGE_SECONDS)
    seats = None if stale else state.get("seats")

    document, pools_error = fetch_pools(timeout=3.0)
    if document is None and isinstance(state.get("pools"), dict):
        document = state["pools"]
    pools = pools_by_id(document)

    leases = pick_leases()
    routable, skipped_entries, paced = class_candidates(
        table,
        args.task_class,
        spec,
        author_vendor=args.author_vendor,
        pools=pools,
        seats=seats,
        served=ServedModels(),
        leases=leases,
    )
    skipped = [f"{entry['seat']} ({entry['reason']})" for entry in skipped_entries]
    if not routable:
        detail = "; ".join(skipped) if skipped else "no chain entry survived filtering"
        print(f"route: nothing routable for class {args.task_class!r}: {detail}", file=sys.stderr)
        return EXIT_UNROUTABLE

    chosen = routable[0]
    reasons = ["first chain entry with the seat up and its pool not exhausted"]
    if args.prefer:
        preferred = next((entry for entry in routable if entry["seat"] == args.prefer), None)
        if preferred is None:
            why = next((entry["reason"] for entry in skipped_entries if entry["seat"] == args.prefer), "unroutable")
            message = f"preferred {args.prefer!r} cannot run: {why}"
            print(f"route: {message}", file=sys.stderr)
            if args.json:
                print(json.dumps({"error": "unroutable", "message": message}))
            return EXIT_UNROUTABLE
        reasons = [f"preferred by caller over {chosen['seat']}"]
        chosen = preferred
    if chosen.get("reserved"):
        reasons.insert(0, f"{chosen['reserved']}; no other seat open")
    if cross_vendor:
        reasons.append(f"cross_vendor: excluded author vendor {args.author_vendor}")
    if stale:
        reasons.append(
            f"ignored routing-state.json: {age and round(age)}s old, over the {STATE_MAX_AGE_SECONDS}s limit"
        )
    if paced:
        reasons.extend(paced)
    if skipped:
        reasons.append("skipped " + "; ".join(skipped))
    if pools_error:
        reasons.append(f"pool status unknown ({pools_error})")
    auditor = chosen["auditor"]
    result = {
        "class": args.task_class,
        "state_age_s": None if age is None else round(age, 1),
        "audit": auditor,
        "auditor": auditor,
        "audit_gap": chosen.get("audit_gap"),
        "lease_availability": "known" if leases is not None else "unknown",
        "reserve": reserve_state(table, pools, args.task_class),
        "seat": chosen["seat"],
        "model": chosen["model"],
        "alias": chosen["alias"],
        "pool": chosen["pool"],
        "reason": "; ".join(reasons),
        "fallbacks": [
            {"seat": entry["seat"], "model": entry["model"], "pool": entry["pool"]}
            for entry in routable
            if entry is not chosen
        ],
    }
    if args.prefer or args.effort:
        result["default"] = routable[0]["seat"]
        result["effort"] = next(
            (
                entry.get("effort")
                for entry in chain
                if isinstance(entry, dict)
                and entry.get("seat") == chosen["seat"]
                and entry.get("model") == chosen["alias"]
            ),
            None,
        )
    record_caller_choice(args, result)
    print_pick(result, as_json=args.json)
    return EXIT_OK


def command_menu(args: argparse.Namespace) -> int:
    menu = build_menu([args.task_class] if args.task_class else None)
    if args.json:
        print(json.dumps(menu, indent=2, sort_keys=True))
    else:
        print(f"menu at {menu['generatedAt']} (pools: {menu['poolsSource'] or menu['poolsError']})")
        for pool_id, pool in menu["pools"].items():
            if pool.get("refills"):
                print(
                    f"{pool_id} refills: "
                    + ", ".join(
                        f"{parse_ts(row['at']):%m-%d %H:%MZ} +{row['accounts']} accounts" for row in pool["refills"]
                    )
                )
        for class_name, entry in menu["classes"].items():
            if entry["driver"]:
                print(f"{class_name:<11} driver session")
                continue
            shown = ", ".join(seat.get("id", seat.get("rung", "?")) for seat in entry["seats"]) or "nothing routable"
            print(f"{class_name:<11} {shown}")
            for excluded in entry["excluded"]:
                name = excluded.get("seat", excluded.get("rung", "?"))
                print(f"{'':<11}   excluded {name}: {excluded['reason']}")
    routable = any(entry["seats"] or entry["driver"] for entry in menu["classes"].values())
    return EXIT_OK if routable else EXIT_UNROUTABLE


def command_policy(args: argparse.Namespace) -> int:
    snapshot = policy_snapshot(load_table(apply_default=False))
    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        print(
            f"policy {snapshot['policy_rev']} table {snapshot['table_sha256'][:12]} "
            f"background {snapshot['background_model']}"
        )
        for seat, spec in snapshot["seats"].items():
            print(f"  {seat}: {spec['model']} {spec['effort'] or ''}".rstrip())
    return EXIT_OK


def outcomes_now() -> datetime:
    configured = parse_ts((os.environ.get("ROUTE_OUTCOMES_NOW") or "").strip())
    return configured if configured else datetime.now(timezone.utc)
