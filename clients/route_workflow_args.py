from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from route_class_candidates import alias_vendor, read_decisions, workflow_relay_seat  # pyrefly: ignore [missing-import]
from route_dispatch import DecisionIndex, merge_events, row_time  # pyrefly: ignore [missing-import]
from route_ladder_walk import merge_receipts_path  # pyrefly: ignore [missing-import]
from route_pace_state import eval_seat_id  # pyrefly: ignore [missing-import]
from route_pick import command_pick, outcomes_now  # pyrefly: ignore [missing-import]
from route_reserve_state import (  # pyrefly: ignore [missing-import]
    CLOSEOUT_DATA_KEYS,
    SUBSCRIPTION_VENDORS,
    WORKFLOW_AUTHOR_VENDORS,
    WORKFLOW_OPUS_STAGES,
    OutcomeSources,
    git_reverts,
    log_value,
    number_or_none,
    outcome_event,
    read_jsonl,
    summed_part,
    workflow_quota,
    workflow_stage_effort,
)
from route_resolve_one import workflow_opus_stage, workflow_seat, workflow_table  # pyrefly: ignore [missing-import]
from route_seat_states import api_request_logs  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_UNROUTABLE,
    ledger_path,
    load_table,
    now_iso,
    table_bytes,
    table_path,
)


def pick_json(table: dict[str, Any], class_name: str, author_vendor: str | None) -> dict[str, Any]:
    args = argparse.Namespace(
        task_class=class_name, author_vendor=author_vendor, skip=[], json=True, prefer=None, effort=None, reason=None
    )
    buffer, errors = io.StringIO(), io.StringIO()
    code = EXIT_OK
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = command_pick(args, table)
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else EXIT_UNROUTABLE
    text = buffer.getvalue().strip()
    result = json.loads(text) if text.startswith("{") else {}
    if code != EXIT_OK or "error" in result or not result.get("seat"):
        message = result.get("message") or errors.getvalue().strip().removeprefix("route: ") or f"exit {code}"
        raise ValueError(message)
    return result


def workflow_pick(table: dict[str, Any], class_name: str, author_vendor: str | None) -> dict[str, Any]:
    picked = pick_json(table, class_name, author_vendor)
    if picked.get("seat") == "driver":
        raise ValueError("driver class: stays on the driver session, no subagent")
    out = workflow_seat(table, class_name, picked)
    fallbacks = []
    for entry in picked.get("fallbacks") or []:
        try:
            fallbacks.append(workflow_seat(table, class_name, entry))
        except ValueError:
            continue
    out["fallbacks"] = fallbacks
    audit = picked.get("audit")
    if isinstance(audit, dict) and audit.get("seat"):
        try:
            out["audit"] = workflow_seat(table, "verify", audit)
        except ValueError as error:
            out["audit_error"] = str(error)
    return out


def command_workflow_args(args: argparse.Namespace) -> int:
    quota = workflow_quota()
    table = workflow_table(load_table())
    classes = table.get("classes") if isinstance(table.get("classes"), dict) else {}
    document: dict[str, Any] = {
        "generated_at": now_iso(),
        "ladder": table.get("ladder"),
        "table_sha256": hashlib.sha256(table_bytes(table_path())).hexdigest() if table_path().exists() else None,
        "quota": quota,
        "policy_rev": table.get("policy_rev"),
        "stages": {},
        "seats": {},
        "review_for": {},
        "errors": {},
    }
    for stage in WORKFLOW_OPUS_STAGES:
        document["stages"][stage] = workflow_opus_stage(table, stage)
        if stage in classes or stage == "judge":
            document["seats"][stage] = document["stages"][stage]
    document["stage_effort"] = workflow_stage_effort(table)
    try:
        document["seats"]["relay"] = workflow_relay_seat(table)
    except ValueError as error:
        document["errors"]["relay"] = str(error)
    for class_name in sorted(classes):
        if class_name == "verify" or class_name in WORKFLOW_OPUS_STAGES:
            continue
        try:
            document["seats"][class_name] = workflow_pick(table, class_name, None)
        except ValueError as error:
            document["errors"][class_name] = str(error)
    for vendor in WORKFLOW_AUTHOR_VENDORS:
        try:
            document["review_for"][vendor] = workflow_pick(table, "verify", vendor)
        except ValueError as error:
            document["errors"][f"review_for.{vendor}"] = str(error)
    print(json.dumps(document, indent=2, sort_keys=True))
    if not document["seats"] and not document["review_for"]:
        return EXIT_UNROUTABLE
    return EXIT_DEGRADED if document["errors"] else EXIT_OK


def live_sources(since: timedelta) -> OutcomeSources:
    now = outcomes_now()
    start = now - since
    receipts = read_jsonl(merge_receipts_path())
    reverts_file = (os.environ.get("ROUTE_OUTCOME_REVERTS") or "").strip()
    logs_file = (os.environ.get("ROUTE_REQUEST_LOGS") or "").strip()
    return OutcomeSources(
        decisions=read_decisions(),
        ledger=read_jsonl(ledger_path()),
        receipts=receipts,
        reverts=read_jsonl(Path(reverts_file).expanduser()) if reverts_file else git_reverts(receipts, start),
        request_logs=read_jsonl(Path(logs_file).expanduser()) if logs_file else api_request_logs(start),
        seeds=[],
        now=now,
    )


def closeout_events(src: OutcomeSources, index: DecisionIndex, start: datetime) -> list[dict[str, Any]]:
    events = []
    for row in src.ledger:
        ts = row_time(row)
        if ts is None or ts < start:
            continue
        decision = index.lookup(row.get("decision_id"))
        if decision is None:
            continue
        if row.get("event") == "closeout":
            key = f"{row.get('session_id')}|{row.get('agent_id') or row.get('name')}|{row.get('ts')}"
            data = {name: row.get(name) for name in CLOSEOUT_DATA_KEYS if name in row}
            event = outcome_event(
                "infra" if row.get("infra_reason") else "closeout", "subagent-closeout", key, ts, decision, data
            )
            if isinstance(row.get("event_id"), str) and re.fullmatch(r"evt_[0-9a-f]{32}", row["event_id"]):
                event["event_id"] = row["event_id"]
            events.append(event)
        elif row.get("event") == "route_fallback" or isinstance(row.get("fallback"), dict):
            fallback = row.get("fallback") if isinstance(row.get("fallback"), dict) else row
            data = {
                "fallback": {
                    "seat": fallback.get("seat"),
                    "model": fallback.get("model"),
                    "reason": fallback.get("reason"),
                },
                "pool_pct": number_or_none(row.get("pool_pct")),
            }
            events.append(
                outcome_event(
                    "infra",
                    "route-fallback",
                    f"{row.get('decision_id')}|{row.get('ts')}|{fallback.get('seat')}",
                    ts,
                    decision,
                    data,
                )
            )
    return events


def revert_events(
    src: OutcomeSources, merges: list[dict[str, Any]], index: DecisionIndex, start: datetime
) -> list[dict[str, Any]]:
    events = []
    for row in src.reverts:
        ts = row_time(row)
        reverted = row.get("reverts")
        targets = [reverted] if isinstance(reverted, str) else [ref for ref in reverted or [] if isinstance(ref, str)]
        if ts is None or ts < start or not targets:
            continue
        for merge in merges:
            shas = (merge["data"].get("merge_sha") or "", merge["data"].get("commit_sha") or "")
            hit = next(
                (
                    target
                    for target in targets
                    if any(sha.startswith(target) or target.startswith(sha) for sha in shas if sha)
                ),
                None,
            )
            if hit is None:
                continue
            decision = index.lookup(merge.get("decision_id")) or {}
            events.append(
                outcome_event(
                    "revert",
                    "git-revert",
                    f"{row.get('sha')}|{merge['job_id']}",
                    ts,
                    decision,
                    {"revert_sha": row.get("sha"), "reverted_sha": hit},
                )
            )
    return events


def repair_events(index: DecisionIndex, start: datetime) -> list[dict[str, Any]]:
    events = []
    for row in index.by_id.values():
        ts = row_time(row)
        if ts is None or ts < start or (row.get("attempt") or 1) != 1:
            continue
        parent = index.parent(row)
        if parent is None:
            continue
        root = index.root(row)
        kind = (
            "repair"
            if row.get("seat") == parent.get("seat") and row.get("class") == parent.get("class")
            else "escalation"
        )
        events.append(
            outcome_event(
                kind,
                "route-decisions",
                str(row["decision_id"]),
                ts,
                row,
                {
                    "child_job": row.get("job_id"),
                    "parent_job": parent.get("job_id"),
                    "root_decision": root.get("decision_id"),
                },
                job_id=str(root.get("job_id")),
            )
        )
    return events


def collect_outcome_events(src: OutcomeSources, since: timedelta | None) -> list[dict[str, Any]]:
    start = src.now - since if since is not None else datetime.min.replace(tzinfo=timezone.utc)
    index = DecisionIndex(src.decisions)
    merges = merge_events(src, index, start)
    seeds = [row for row in src.seeds if row.get("v") == 1 and isinstance(row.get("event_id"), str)]
    return (
        closeout_events(src, index, start)
        + merges
        + revert_events(src, merges, index, start)
        + repair_events(index, start)
        + seeds
    )


def worker_cost_source(table: dict[str, Any], seat: str | None, model: str | None) -> str:
    seat = (seat or "").lower()
    if seat.startswith("cursor-"):
        return "cursor_pool"
    if seat.startswith(("muse-", "devin-")):
        return "subscription"
    vendor = alias_vendor(table, model) if model else None
    if vendor == "cursor":
        return "cursor_pool"
    if vendor in SUBSCRIPTION_VENDORS:
        return "subscription"
    return "request_logs"


def pool_part(part: str, source: str, pool_pct: float | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"part": part, "source": source, "usd": None}
    if source == "cursor_pool":
        entry["pool_pct"] = pool_pct
    return entry


class JobView:
    def __init__(self, job_id: str, decisions: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
        self.job_id = job_id
        self.decisions = decisions
        self.first = decisions[0]
        self.events = sorted(events, key=lambda event: (event["ts"], event["event_id"]))

    def kind(self, *kinds: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["kind"] in kinds]

    def fallback(self) -> dict[str, Any] | None:
        for event in reversed(self.kind("infra", "closeout")):
            value = event["data"].get("fallback")
            if isinstance(value, dict) and (value.get("seat") or value.get("model")):
                return {"seat": value.get("seat"), "model": value.get("model"), "reason": value.get("reason")}
        return None

    def latest(self, key: str) -> Any:
        for event in reversed(self.kind("closeout", "infra")):
            value = event["data"].get(key)
            if value not in (None, "", []):
                return value
        return None

    def tagged(self, key: str) -> Any:
        for event in reversed(self.kind("verdict", "closeout")):
            value = event[key] if key in event else event["data"].get(key)
            if value not in (None, "", []):
                return value
        return None

    def session_ids(self, decision_id: str | None = None) -> list[str]:
        found: set[str] = set()
        for event in self.kind("closeout", "infra"):
            if decision_id is None or event.get("decision_id") == decision_id:
                found.update(item for item in event["data"].get("session_ids") or [] if isinstance(item, str) and item)
        return sorted(found)


def command_eval_seat(args: argparse.Namespace) -> int:
    print(eval_seat_id(args.decision_id))
    return EXIT_OK


def job_own_parts(table: dict[str, Any], view: JobView, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fallback = view.fallback()
    seat = (fallback or {}).get("seat") or view.first.get("seat")
    model = (fallback or {}).get("model") or view.first.get("model_alias")
    source = worker_cost_source(table, seat, model)
    worker_vendor = alias_vendor(table, model) if model else None
    wrapper = view.latest("wrapper_model") or view.first.get("wrapper_model")
    pool_pct = number_or_none(view.latest("pool_pct"))
    has_wrapper = bool(wrapper) and not (source == "request_logs" and worker_vendor == "anthropic")
    parts = []
    for decision in view.decisions:
        decision_id = decision.get("decision_id")
        sessions = set(view.session_ids(decision_id))
        rows = [
            row
            for row in logs
            if log_value(row, "caller_seat", "callerSeat") in (decision_id, eval_seat_id(decision_id))
            or (sessions and log_value(row, "session_id", "sessionId") in sessions)
        ]
        wrapper_rows = [row for row in rows if has_wrapper and str(log_value(row, "model") or "").startswith("claude-")]
        worker_rows = [row for row in rows if not any(row is other for other in wrapper_rows)]
        if (decision.get("attempt") or 1) > 1:
            if source == "request_logs":
                parts.append(summed_part("retry", rows))
            else:
                parts.append(pool_part("retry", source, pool_pct))
                if has_wrapper:
                    parts.append(summed_part("retry", wrapper_rows))
            continue
        parts.append(
            summed_part("worker", worker_rows) if source == "request_logs" else pool_part("worker", source, pool_pct)
        )
        if has_wrapper:
            parts.append(summed_part("wrapper", wrapper_rows))
    return parts


def parts_total(parts: list[dict[str, Any]]) -> tuple[float | None, float]:
    known = round(sum(part["usd"] for part in parts if part["usd"] is not None), 6)
    return (known if all(part["usd"] is not None for part in parts) else None), known


def outcome_lines(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def stored_events(path: Path) -> list[dict[str, Any]]:
    return [row for row in read_jsonl(path) if row.get("v") == 1 and isinstance(row.get("event_id"), str)]


def outcome_summary(outcomes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in outcomes:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return dict(sorted(counts.items()))


def print_outcomes(document: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(document, sort_keys=True))
        return
    for row in document.get("outcomes", []):
        cost = "?" if row["cost_usd"] is None else f"${row['cost_usd']:.2f}"
        print(f"{row['job_id']} {row['class']} {row['seat']} {row['status']} {cost}")
    print(" ".join(f"{key}={value}" for key, value in document.get("summary", {}).items()) or "no jobs")


def probe(name: str, started: float, *, ok: bool, error: str | None, **detail: Any) -> dict[str, Any]:
    record = {"name": name, "ok": ok, "latency_ms": int((time.monotonic() - started) * 1000), "error": error}
    record.update(detail)
    return record
