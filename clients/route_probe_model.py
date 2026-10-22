from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from route_anthropic_models import http_get, ledger_now_iso, read_state  # pyrefly: ignore [missing-import]
from route_build_outcomes import apply_cli_seat_holds, probe_command, probe_lb_health, probe_pools  # pyrefly: ignore [missing-import]
from route_pace_state import record_pool_history  # pyrefly: ignore [missing-import]
from route_quota import print_equivalent_note, print_report_quota  # pyrefly: ignore [missing-import]
from route_reserve_state import pools_by_id  # pyrefly: ignore [missing-import]
from route_resolve_one import IMPLEMENT_EVENT, RowKey  # pyrefly: ignore [missing-import]
from route_seat import fetch_pools, http_post  # pyrefly: ignore [missing-import]
from route_seat_states import (  # pyrefly: ignore [missing-import]
    alert_text,
    eval_arm,
    load_eval_standings,
    message_head,
    ok_rate,
    pool_verdict,
    probed_models,
    read_ledger,
    recent_model_traffic,
    seat_states,
    session_costs,
)
from route_storage import atomic_write  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    CODEX_TIMEOUT_SECONDS,
    CURSOR_TIMEOUT_SECONDS,
    DEVIN_TIMEOUT_SECONDS,
    DOCTOR_BUDGET_SECONDS,
    EXIT_DEGRADED,
    EXIT_OK,
    PROBE_BUDGET_SECONDS,
    SESSION_WINDOW_MINUTES,
    alert_path,
    codex_command,
    cursor_command,
    devin_command,
    ledger_path,
    load_table,
    now_iso,
    parse_ts,
    rules_status,
    state_path,
)


def probe_model(
    model: str,
    *,
    table: dict[str, Any],
    traffic: dict[str, dict[str, Any]],
    pools: dict[str, Any],
    allow_probe: bool = True,
) -> dict[str, Any]:

    name = f"model:{model}"
    recent = traffic.get(model)
    if recent is not None:
        return {
            "name": name,
            "ok": True,
            "latency_ms": None,
            "error": None,
            "evidence": "recent_success" if recent["clean"] else "recent_success_with_errors",
            "note": (
                f"{recent['requests']} request(s) on {model} in the last {SESSION_WINDOW_MINUTES}m"
                f", {recent['errors']} session error(s), last seen {recent['lastSeen']}"
            ),
        }
    if not allow_probe:
        return pool_verdict(
            model, table, pools, note="no recent traffic; probe dropped after the 2s budget", latency_ms=None
        )

    started = time.monotonic()
    payload = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}
    result = http_post("/v1/messages", payload, timeout=PROBE_BUDGET_SECONDS)
    latency = int((time.monotonic() - started) * 1000)
    status = result.status
    head = message_head(result.body)

    if result.ok and status == 200:
        return {"name": name, "ok": True, "latency_ms": latency, "error": None, "evidence": "probe_success"}
    if status in (401, 403):
        presence = http_get("/api/models", timeout=4.0)
        ok = presence.ok and presence.status == 200
        return {
            "name": name,
            "ok": ok,
            "latency_ms": latency,
            "error": None if ok else (presence.error or f"HTTP {presence.status}"),
            "evidence": "api_models_presence",
            "note": "/v1/messages needs a credential this probe does not hold; probed /api/models instead",
        }
    if status in (400, 404):
        return {
            "name": name,
            "ok": False,
            "latency_ms": latency,
            "error": f"HTTP {status}: {head or 'model not recognised by the LB'}",
            "evidence": "unknown_model",
        }
    if status == 429:
        return pool_verdict(
            model, table, pools, note=f"fresh selection 429: {head or 'no account selectable'}", latency_ms=latency
        )
    if result.error == "timeout":
        return pool_verdict(
            model, table, pools, note=f"probe exceeded the {PROBE_BUDGET_SECONDS:.0f}s budget", latency_ms=latency
        )
    return {
        "name": name,
        "ok": False,
        "latency_ms": latency,
        "error": f"HTTP {status}" if status else (result.error or "request failed"),
        "evidence": "probe_failure",
    }


def command_doctor(args: argparse.Namespace) -> int:
    started = time.monotonic()
    table = load_table()
    probes: list[dict[str, Any]] = [probe_lb_health()]
    pools_probe, pools_document = probe_pools()
    if args.write and pools_document is not None:
        record_pool_history(pools_document, datetime.now(timezone.utc))
    probes.append(pools_probe)
    probes.append(probe_command("cursor-agent", cursor_command(), CURSOR_TIMEOUT_SECONDS))
    probes.append(probe_command("devin", devin_command(), DEVIN_TIMEOUT_SECONDS))
    probes.append(probe_command("codex-companion", codex_command(), CODEX_TIMEOUT_SECONDS))

    traffic = recent_model_traffic()
    pools = pools_by_id(pools_document)
    allow_probe = True
    models = probed_models(table)
    for model in models:
        if time.monotonic() - started > DOCTOR_BUDGET_SECONDS - PROBE_BUDGET_SECONDS:
            allow_probe = False
        probe = probe_model(model, table=table, traffic=traffic, pools=pools, allow_probe=allow_probe)
        if probe.get("evidence") == "pool_status" and "budget" in (probe.get("note") or ""):
            allow_probe = False
        probes.append(probe)

    by_name = {probe["name"]: probe for probe in probes}
    seats = seat_states(table, by_name, models)
    apply_cli_seat_holds(seats, read_state())
    state = {
        "ts": ledger_now_iso(),
        "seats": seats,
        "pools": pools_document,
        "probes": probes,
        "trafficWindowMinutes": SESSION_WINDOW_MINUTES,
        "auth": "loopback_unauthenticated",
        "rules": rules_status(),
        "elapsed_s": round(time.monotonic() - started, 2),
    }
    degraded = any(not probe["ok"] for probe in probes)

    if args.write:
        atomic_write(state_path(), json.dumps(state, indent=2, sort_keys=True) + "\n")
        if degraded:
            atomic_write(alert_path(), alert_text(state))
        else:
            try:
                alert_path().unlink()
            except FileNotFoundError:
                pass
        print(f"route doctor: {'DEGRADED' if degraded else 'ok'} -> {state_path()}")
    else:
        print(json.dumps(state, indent=2, sort_keys=True))
    return EXIT_DEGRADED if degraded else EXIT_OK


def aggregate(dispatches: list[dict[str, Any]], closeouts: list[dict[str, Any]]) -> dict[RowKey, dict[str, Any]]:

    pending: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    rows: dict[RowKey, dict[str, Any]] = {}

    def row_for(record: dict[str, Any]) -> dict[str, Any]:
        key: RowKey = (
            str(record.get("task_class") or "unknown"),
            str(record.get("subagent_type") or "unknown"),
            str(record.get("model") or "unknown"),
        )
        return rows.setdefault(
            key,
            {
                "class": key[0],
                "seat": key[1],
                "model": key[2],
                "dispatches": 0,
                "denied": 0,
                "closeouts": 0,
                "ok": 0,
                "durations": [],
                "sessions": set(),
                "tokens_in": 0,
                "tokens_out": 0,
            },
        )

    for record in dispatches:
        row = row_for(record)
        if record.get("denied"):
            row["denied"] += 1
            continue
        row["dispatches"] += 1
        if record.get("session_id"):
            row["sessions"].add(record["session_id"])
        pending.setdefault((record.get("session_id"), record.get("name")), []).append(record)

    for record in closeouts:
        key = (record.get("session_id"), record.get("name"))
        queue = pending.get(key) or []
        origin = queue.pop(0) if queue else None
        merged = dict(record)
        if origin is not None:
            merged.setdefault("task_class", origin.get("task_class"))
            merged.setdefault("subagent_type", origin.get("subagent_type"))

        requested = merged.get("dispatch_model") or (origin or {}).get("model")
        if requested or "dispatch_model" in merged:
            merged["model"] = requested
        row = row_for(merged)
        row["closeouts"] += 1
        if merged.get("ok") is True:
            row["ok"] += 1
        duration = merged.get("duration_s")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            row["durations"].append(float(duration))
        if merged.get("session_id"):
            row["sessions"].add(merged["session_id"])
        for field in ("tokens_in", "tokens_out"):
            value = merged.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                row[field] += value
    return rows


def command_report(args: argparse.Namespace) -> int:
    import statistics

    print_report_quota(fetch_pools)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    dispatches, closeouts = read_ledger(since)
    rows = aggregate(dispatches, closeouts)
    if getattr(args, "implement", False):
        print_implement_report(since, args.days)
        return EXIT_OK
    if not rows:
        print(f"No dispatches in {ledger_path()} within {args.days} day(s).")
        print_implement_report(since, args.days)
        return EXIT_OK
    every_session: set[str] = set()
    for row in rows.values():
        every_session |= row["sessions"]
    costs = session_costs(every_session)

    print(f"# route report — {args.days} day(s) to {now_iso()}\n")
    print(f"Ledger: `{ledger_path()}` · {len(dispatches)} dispatches · {len(closeouts)} closeouts\n")
    print(
        "| class | seat | model | dispatches | denied | closeouts | ok-rate | median s | tokens in | tokens out "
        "| sessions | API-equiv $ |"
    )
    print("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for key in sorted(rows):
        row = rows[key]
        rate = ok_rate(row)
        median = f"{statistics.median(row['durations']):.0f}" if row["durations"] else "-"
        cost = sum(costs.get(session, 0.0) for session in row["sessions"])
        cost_text = f"${cost:.2f}" if cost else "-"
        print(
            f"| {row['class']} | {row['seat']} | {row['model']} | {row['dispatches']} | {row['denied']} | "
            f"{row['closeouts']} | {'-' if rate is None else f'{rate:.0%}'} | {median} | "
            f"{row['tokens_in']:,} | {row['tokens_out']:,} | {len(row['sessions'])} | {cost_text} |"
        )
    print_equivalent_note(costs, every_session)
    print_implement_report(since, args.days)
    return EXIT_OK


def command_record(args: argparse.Namespace) -> int:

    record = {
        "ts": ledger_now_iso(),
        "event": IMPLEMENT_EVENT,
        "class": args.task_class,
        "task": args.task,
        "seat": args.seat,
        "model": args.model,
        "tokens_in": args.tokens_in,
        "tokens_out": args.tokens_out,
        "wall_s": args.wall_s,
        "audit": args.audit,
        "rework": args.rework,
        "tests": args.tests,
        "notes": args.notes,
    }
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"recorded {args.task_class}/{args.seat}/{args.model} in {path}")
    return EXIT_OK


def implement_results(since: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = ledger_path().read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return rows
    for line in lines:
        if IMPLEMENT_EVENT not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        stamp = parse_ts(record.get("ts"))
        if (
            record.get("event") == IMPLEMENT_EVENT
            and record.get("class", "implement") == "implement"
            and stamp is not None
            and stamp >= since
        ):
            rows.append(record)
    return rows


def print_implement_report(since: datetime, days: int) -> None:
    import statistics

    results = implement_results(since)
    print(f"\n## Implement comparison — {days} day(s)\n")
    if not results:
        print("No implement_result rows yet (`route record` writes them).")
        return

    tasks: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in results:
        key = (str(record.get("seat")), str(record.get("model")), str(record.get("task")))
        task = tasks.setdefault(key, {"attempts": 0, "tokens_in": 0, "tokens_out": 0, "wall_s": 0.0, "rework": 0})
        task["attempts"] += 1
        task["audit"] = record.get("audit")
        for field in ("tokens_in", "tokens_out"):
            if isinstance(record.get(field), int) and not isinstance(record.get(field), bool):
                task[field] += record[field]
        if isinstance(record.get("wall_s"), (int, float)):
            task["wall_s"] += float(record["wall_s"])
        if isinstance(record.get("rework"), int):
            task["rework"] = max(task["rework"], record["rework"])
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (seat, model, _task), task in tasks.items():
        task["rework"] = max(task["rework"], task["attempts"] - 1)
        groups.setdefault((seat, model), []).append(task)
    print("| seat | model | tasks | audit pass | mean rework | median wall s | tokens in | tokens out | tokens/task |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for (seat, model), group in sorted(groups.items()):
        passes = sum(1 for task in group if task.get("audit") == "pass")
        tin = sum(task["tokens_in"] for task in group)
        tout = sum(task["tokens_out"] for task in group)
        walls = [task["wall_s"] for task in group if task["wall_s"]]
        per_task = f"{(tin + tout) / len(group):,.0f}" if (tin or tout) else "-"
        mean_rework = f"{statistics.mean(task['rework'] for task in group):.1f}"
        median_wall = f"{statistics.median(walls):.0f}" if walls else "-"
        print(
            f"| {seat} | {model} | {len(group)} | {passes}/{len(group)} | {mean_rework} | "
            f"{median_wall} | {tin:,} | {tout:,} | {per_task} |"
        )


def eval_gate(
    class_name: str, candidate_arm: str, default_arm: str, standings: dict[str, Any] | None = None
) -> dict[str, Any]:
    standings = load_eval_standings() if standings is None else standings
    candidate = eval_arm(standings, class_name, candidate_arm, default_arm)
    default = eval_arm(standings, class_name, default_arm, default_arm)
    evidence = {"candidate": candidate, "default": default}
    if (
        candidate is None
        or default is None
        or not candidate["task_version_set"]
        or candidate["task_version_set"] != default["task_version_set"]
    ):
        return {"veto": False, "reason": "no-eval-evidence", "evidence": evidence}
    if candidate["upper_bound"] < default["per_trial_rate"]:
        return {
            "veto": True,
            "evidence": evidence,
            "reason": f"eval per-trial upper bound {candidate['upper_bound']:.1%} is below "
            f"default {default['per_trial_rate']:.1%}",
        }
    if candidate["solved_all3"] < default["solved_all3"] - 1:
        return {
            "veto": True,
            "evidence": evidence,
            "reason": f"eval solved_all3 {candidate['solved_all3']} is below default {default['solved_all3']} - 1",
        }
    return {"veto": False, "reason": "eval-ok", "evidence": evidence}
