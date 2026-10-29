from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from route_anthropic_models import http_get, ledger_now_iso  # pyrefly: ignore [missing-import]
from route_class_candidates import outcome_events_path, outcomes_path  # pyrefly: ignore [missing-import]
from route_dispatch import DecisionIndex, replay_sources, row_time  # pyrefly: ignore [missing-import]
from route_pace_state import state_age_seconds  # pyrefly: ignore [missing-import]
from route_reserve_state import (  # pyrefly: ignore [missing-import]
    LB_HEALTH_ATTEMPTS,
    LB_HEALTH_RETRY_SECONDS,
    MERGE_CLASSES,
    METRIC_CLASSES,
    REDISPATCH_WINDOW,
    REPAIR_WINDOW,
    VERDICT_CLASSES,
    OutcomeSources,
    number_or_none,
    parse_since,
    utc_stamp,
)
from route_seat import fetch_pools  # pyrefly: ignore [missing-import]
from route_seat_run_import import baseline_outcomes, baseline_path  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    CLI_SEAT_HOLD_AFTER_FAILURES,
    CLI_SEAT_HOLD_SECONDS,
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_UNROUTABLE,
    SEAT_PROBE,
    STATE_MAX_AGE_SECONDS,
    load_table,
    parse_ts,
)
from route_workflow_args import (  # pyrefly: ignore [missing-import]
    JobView,
    collect_outcome_events,
    job_own_parts,
    live_sources,
    outcome_lines,
    outcome_summary,
    parts_total,
    print_outcomes,
    probe,
    stored_events,
    write_atomic,
)


def job_status(view: JobView, index: DecisionIndex, now: datetime) -> tuple[str, str]:
    cls = str(view.first.get("class"))
    closeouts = view.kind("closeout")
    merges = view.kind("merge")
    failures = [event for event in view.kind("infra") if event["data"].get("infra_reason")]
    rule = (
        "merged_no_repair_72h"
        if cls in MERGE_CLASSES
        else "verdict_matches"
        if cls in VERDICT_CLASSES
        else "metric_moved"
        if cls in METRIC_CLASSES
        else "closeout_ok_no_redispatch_2h"
    )
    if not closeouts and not merges:
        return ("infra_failure" if failures else "pending"), rule
    last_attempt = max((decision.get("attempt") or 1) for decision in view.decisions)
    last_close = closeouts[-1] if closeouts else None
    awaiting = last_close is None or (last_close.get("attempt") or 1) < last_attempt
    failed = last_close is not None and not last_close["data"].get("ok", False) and not awaiting
    if rule == "merged_no_repair_72h":
        merged_at = parse_ts(merges[0]["ts"]) if merges else None
        horizon = merged_at + REPAIR_WINDOW if merged_at else None
        trouble = [
            event
            for event in view.kind("repair", "escalation", "revert")
            if horizon is None or parse_ts(event["ts"]) <= horizon
        ]
        if trouble:
            return "rejected", rule
        if merged_at is not None:
            return ("accepted" if now >= horizon else "pending"), rule  # pyrefly: ignore [unsupported-operation]
        return ("rejected" if failed else "pending"), rule
    if view.kind("repair", "escalation"):
        return "rejected", rule
    if rule == "verdict_matches":
        verdicts = view.kind("verdict")
        if verdicts:
            data = verdicts[-1]["data"]
            matches = (
                data["matches"]
                if isinstance(data.get("matches"), bool)
                else (data.get("verdict") is not None and data.get("verdict") == data.get("expected"))
            )
            return ("accepted" if matches else "rejected"), rule
        return ("rejected" if failed else "pending"), rule
    if rule == "metric_moved":
        metrics = view.kind("metric")
        if metrics:
            data = metrics[-1]["data"]
            delta = number_or_none(data.get("delta"))
            if delta is None and number_or_none(data.get("before")) is not None:
                after = number_or_none(data.get("after"))
                delta = after - float(data["before"]) if after is not None else None
            moved = delta is not None and (
                (delta > 0)
                if data.get("direction") == "up"
                else (delta < 0)
                if data.get("direction") == "down"
                else False
            )
            return ("accepted" if moved else "rejected"), rule
        return ("rejected" if failed else "pending"), rule
    if failed:
        return "rejected", rule
    if awaiting or last_close is None:
        return "pending", rule
    closed_at = parse_ts(last_close["ts"])
    caller = view.first.get("caller")
    if caller:
        for rows in index.by_job.values():
            other = rows[0]
            stamp = row_time(other)
            if (
                other.get("job_id") != view.job_id
                and other.get("caller") == caller
                and other.get("class") == cls
                and stamp is not None
                and closed_at < stamp <= closed_at + REDISPATCH_WINDOW
            ):
                return "rejected", rule
    return ("accepted" if now >= closed_at + REDISPATCH_WINDOW else "pending"), rule


def build_outcomes(
    table: dict[str, Any],
    events: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    logs: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    baseline = [row for row in decisions if row.get("kind") == "route.decision.baseline"]
    decisions = [row for row in decisions if row.get("kind") != "route.decision.baseline"]
    index = DecisionIndex([row for row in decisions if row.get("v") == 1 and row.get("decision_id")])
    unique: dict[str, dict[str, Any]] = {}
    for event in events:
        unique.setdefault(event["event_id"], event)
    by_job: dict[str, list[dict[str, Any]]] = {}
    for event in unique.values():
        by_job.setdefault(str(event.get("job_id")), []).append(event)
    views = {job: JobView(job, rows, by_job.get(job, [])) for job, rows in index.by_job.items()}
    own = {job: job_own_parts(table, view, logs) for job, view in views.items()}
    children: dict[str, list[str]] = {}
    for view in views.values():
        for event in view.kind("repair", "escalation"):
            child = event["data"].get("child_job")
            if isinstance(child, str) and child in views and child != view.job_id:
                children.setdefault(view.job_id, []).append(child)
    rows = []
    for job, view in sorted(views.items()):
        status, rule = job_status(view, index, now)
        parts = list(own[job])
        for child in sorted(set(children.get(job, []))):
            child_cost, _ = parts_total(own[child])
            parts.append(
                {"part": "repair", "source": "request_logs" if child_cost is not None else "unknown", "usd": child_cost}
            )
        cost, known = parts_total(parts)
        fallback = view.fallback()
        walls = [number_or_none(event["data"].get("wall_s")) for event in view.kind("closeout", "infra")]
        walls = [wall for wall in walls if wall is not None]
        infra = [
            event["data"].get("infra_reason") or (event["data"].get("fallback") or {}).get("reason")
            for event in view.kind("infra")
        ]
        infra = [reason for reason in infra if reason]
        settled = {event.get("decision_id") for event in view.kind("closeout", "infra")}
        first = view.first
        row = {
            "v": 1,
            "job_id": job,
            "decision_ids": [str(decision["decision_id"]) for decision in view.decisions],
            "class": str(first.get("class")),
            "arm": first.get("arm") if first.get("arm") in ("default", "explore", "explicit") else "default",
            "arm_id": str(first.get("arm_id") or "standard"),
            "propensity": float(first.get("propensity", 1.0)),
            "policy_rev": str(first.get("policy_rev") or "unversioned"),
            "seat": str(first.get("seat")),
            "worker_model": view.latest("worker_model") or (fallback or {}).get("model") or first.get("model"),
            "worker_effort_requested": first.get("effort"),
            "worker_effort_effective": view.latest("worker_effort"),
            "wrapper_model": view.latest("wrapper_model") or first.get("wrapper_model"),
            "fallback": fallback,
            "status": status,
            "outcome": status,
            "ts": view.events[-1]["ts"] if view.events else first.get("ts"),
            "eligible_arms": first.get("eligible_arms", []),
            "success_rule": rule,
            "repairs": len(view.kind("repair")),
            "escalations": len(view.kind("escalation")),
            "infra_reason": infra[-1] if infra else None,
            "merge_shas": sorted({str(event["data"].get("merge_sha")) for event in view.kind("merge")}),
            "cost_usd": cost,
            "cost_known_usd": known,
            "cost_known": cost is not None,
            "cost_parts": parts,
            "attribution_complete": cost is not None
            and all(decision.get("decision_id") in settled for decision in view.decisions),
            "wall_s": round(sum(walls), 1) if walls else None,
            "session_ids": view.session_ids(),
            "evidence": [event["event_id"] for event in view.events],
        }
        if view.tagged("source") == "eval":
            row["source"] = "eval"
        for key in ("task_version", "validation_hash", "eligible_arms"):
            value = view.tagged(key)
            if value is not None:
                row[key] = value
        rows.append(row)
    return rows + baseline_outcomes(baseline)


def ingest_outcomes(
    table: dict[str, Any],
    src: OutcomeSources,
    since: timedelta | None,
    events_file: Path,
    outcomes_file: Path,
    write: bool,
    baseline: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing = stored_events(events_file)
    seen = {row["event_id"] for row in existing}
    fresh = []
    for event in collect_outcome_events(src, since):
        if event["event_id"] not in seen:
            seen.add(event["event_id"])
            fresh.append(event)
    outcomes = build_outcomes(table, existing + fresh, src.decisions + (baseline or []), src.request_logs, src.now)
    if write:
        if fresh:
            events_file.parent.mkdir(parents=True, exist_ok=True)
            with events_file.open("a", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(event, sort_keys=True) + "\n" for event in fresh))
        write_atomic(outcomes_file, outcome_lines(outcomes))
    return fresh, outcomes


def command_outcomes(args: argparse.Namespace) -> int:
    import tempfile

    table = load_table()
    baseline = []
    if args.with_baseline and baseline_path().exists():
        baseline = [json.loads(line) for line in baseline_path().read_text().splitlines() if line.strip()]
        baseline = [row for row in baseline if row.get("v") == 1 and row.get("kind") == "route.decision.baseline"]
    if args.action == "replay":
        if not args.directory or not Path(args.directory).is_dir():
            print(f"route: outcomes replay needs a fixture directory, got {args.directory!r}", file=sys.stderr)
            return EXIT_UNROUTABLE
        src = replay_sources(Path(args.directory))
        with tempfile.TemporaryDirectory(prefix="route-outcomes-") as scratch:
            events_file = Path(scratch) / "outcome-events.jsonl"
            outcomes_file = Path(scratch) / "outcomes.jsonl"
            first_new, _ = ingest_outcomes(table, src, None, events_file, outcomes_file, True, baseline)
            first_text = outcomes_file.read_text(encoding="utf-8")
            second_new, outcomes = ingest_outcomes(table, src, None, events_file, outcomes_file, True, baseline)
            idempotent = outcomes_file.read_text(encoding="utf-8") == first_text and not second_new
            events = stored_events(events_file)
        document = {
            "now": utc_stamp(src.now),
            "idempotent": idempotent,
            "events": events,
            "new_events": [len(first_new), len(second_new)],
            "outcomes": outcomes,
            "summary": outcome_summary(outcomes),
        }
        print_outcomes(document, args.json)
        return EXIT_OK if idempotent else EXIT_DEGRADED
    if args.directory:
        print(f"route: outcomes {args.action} takes no directory", file=sys.stderr)
        return EXIT_UNROUTABLE
    try:
        since = parse_since(args.since)
    except ValueError as error:
        print(f"route: {error}", file=sys.stderr)
        return EXIT_UNROUTABLE
    src = live_sources(since)
    if args.action == "ingest":
        fresh, outcomes = ingest_outcomes(
            table, src, since, outcome_events_path(), outcomes_path(), args.write, baseline
        )
        document = {
            "new_events": len(fresh),
            "written": args.write,
            "outcomes": outcomes,
            "summary": outcome_summary(outcomes),
        }
    else:
        outcomes = build_outcomes(
            table, stored_events(outcome_events_path()), src.decisions + baseline, src.request_logs, src.now
        )
        if args.write:
            write_atomic(outcomes_path(), outcome_lines(outcomes))
        document = {"written": args.write, "outcomes": outcomes, "summary": outcome_summary(outcomes)}
    print_outcomes(document, args.json)
    return EXIT_OK


def probe_lb_health() -> dict[str, Any]:

    started = time.monotonic()
    for attempt in range(LB_HEALTH_ATTEMPTS):
        result = http_get("/health", timeout=4.0)
        path = "/health"
        ok = result.ok and result.status == 200
        if ok or attempt == LB_HEALTH_ATTEMPTS - 1:
            break
        time.sleep(LB_HEALTH_RETRY_SECONDS)
    error = None if ok else (result.error or f"HTTP {result.status}")
    return probe("lb-health", started, ok=ok, error=error, path=path, attempts=attempt + 1)


def probe_pools() -> tuple[dict[str, Any], dict[str, Any] | None]:
    started = time.monotonic()
    document, error = fetch_pools()
    ok = document is not None
    record = probe(
        "lb-pools",
        started,
        ok=ok,
        error=error,
        source=document.get("source") if document else None,
    )
    return record, document


def probe_command(name: str, command: list[str], timeout: float) -> dict[str, Any]:

    started = time.monotonic()
    if not command:
        return probe(name, started, ok=False, error="probe command is empty")
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return probe(name, started, ok=False, error=f"timeout after {timeout:.0f}s")
    except OSError as error:
        return probe(name, started, ok=False, error=f"{type(error).__name__}: {error}")
    ok = completed.returncode == 0
    error = None if ok else f"exit {completed.returncode}"
    return probe(name, started, ok=ok, error=error)


def apply_cli_seat_holds(seats: dict[str, Any], previous: dict[str, Any]) -> None:

    age = state_age_seconds(previous)
    fresh = age is not None and age <= STATE_MAX_AGE_SECONDS
    prior_seats = previous.get("seats") if fresh and isinstance(previous.get("seats"), dict) else {}
    now = datetime.now(timezone.utc)
    for seat in SEAT_PROBE:
        record = seats.get(seat)
        if not isinstance(record, dict) or record["ok"]:
            continue
        prior = prior_seats.get(seat)
        count = prior.get("failures") if isinstance(prior, dict) else None
        failures = (count if isinstance(count, int) and not isinstance(count, bool) else 0) + 1
        record["failures"] = failures
        if failures < CLI_SEAT_HOLD_AFTER_FAILURES:
            record["ok"] = True
            record["note"] = (
                f"probe failed ({record['error']}); {failures} consecutive failure(s), "
                f"held only after {CLI_SEAT_HOLD_AFTER_FAILURES}"
            )
        else:
            record["hold_until"] = (now + timedelta(seconds=CLI_SEAT_HOLD_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def learning_entry(class_name: str, action: str, arm_id: str, evidence: dict[str, Any], reason: str) -> dict[str, Any]:
    import secrets

    return {
        "class": class_name,
        "action": action,
        "arm_id": arm_id,
        "evidence": evidence,
        "reason": reason,
        "ts": ledger_now_iso(),
        "id": secrets.token_hex(12),
    }
