from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from route_resolve_one import decisions_path  # pyrefly: ignore [missing-import]
from route_versioned_rules import load_table  # pyrefly: ignore [missing-import]

SOURCE_SCRIPT = r"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).expanduser()
if not root.is_dir():
    raise SystemExit("seat-run source directory does not exist")
fields = "key job job_id seat model model_alias effective_model effective_seat effort worker_model worker_effort class klass caller caller_host caller_lane lane box node submitted started_at ended_at start end status outcome exit_status exit_code rc code at seconds wall_s verdict ok tokens_in tokens_out input_tokens output_tokens cache_read_tokens cache_read total_tokens".split()
paths = sorted(root.glob("*.job.json"))
for directory in sorted(root.iterdir()):
    if directory.is_dir():
        for name in ("job.json", "record.json", "meta.json"):
            candidate = directory / name
            if candidate.is_file():
                paths.append(candidate)
                break
invalid = []
for path in paths:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("not an object")
    except (OSError, UnicodeError, ValueError):
        invalid.append(str(path.relative_to(root)))
        continue
    def project(value):
        return {key: value[key] for key in fields if key in value} if isinstance(value, dict) else {}
    row = project(record)
    row["source_record"] = str(path.relative_to(root))
    row["job_id"] = record.get("key") or record.get("job_id") or (path.name[:-9] if path.name.endswith(".job.json") else path.parent.name)
    for key in ("final", "request"):
        value = record.get(key)
        if isinstance(value, dict):
            row[key] = project(value)
            receipt = value.get("receipt")
            if isinstance(receipt, dict):
                row[key]["receipt"] = project(receipt)
                row[key]["receipt"]["handoff_data"] = project(receipt.get("handoff_data"))
    argv = record.get("argv") or []
    options = {}
    for index, word in enumerate(argv):
        if not isinstance(word, str):
            continue
        name, equals, value = word.partition("=")
        if name in ("--seat", "--model", "--effort", "--class", "--caller"):
            if equals:
                options[name[2:]] = value
            elif index + 1 < len(argv):
                options[name[2:]] = argv[index + 1]
    row["options"] = options
    print(json.dumps({"record": row}, sort_keys=True), flush=True)
print(json.dumps({"summary": {"files": len(paths), "invalid": len(invalid), "invalid_samples": invalid[:5]}}, sort_keys=True))
"""


def baseline_path() -> Path:
    return Path.home() / ".claude/logs/route-decisions-seat-run-baseline.jsonl"


def first_value(rows: list[dict[str, Any]], *keys: str) -> Any:
    return next((row[key] for row in rows for key in keys if row.get(key) is not None and row[key] != ""), None)


def timestamp(value: Any) -> str | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            stamp = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str) and value:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        else:
            return None
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def baseline_row(record: dict[str, Any], host: str, table: dict[str, Any]) -> dict[str, Any]:
    final = record.get("final") or {}
    receipt = final.get("receipt") or {}
    handoff = receipt.get("handoff_data") or {}
    request = record.get("request") or {}
    options = record.get("options") or {}
    identity = [receipt, record, request, options]
    seat = first_value(identity, "effective_seat", "seat")
    candidates = [
        name
        for name, spec in table.get("classes", {}).items()
        if any(entry.get("seat") == seat for entry in spec.get("chain", []) if isinstance(entry, dict))
    ]
    task_class = first_value([record, request, options, receipt], "class", "klass")
    if task_class is None and len(candidates) == 1:
        task_class = candidates[0]
    job_id = str(record["job_id"])
    started = first_value([record, receipt], "started_at", "start")
    start_source = "started_at" if started is not None else "submitted"
    started = timestamp(started if started is not None else record.get("submitted"))
    ended = first_value([record, receipt], "ended_at", "end")
    end_source = "ended_at" if ended is not None else "final.at"
    ended = timestamp(ended if ended is not None else final.get("at"))
    model = first_value(identity, "effective_model", "worker_model", "model")
    row = {
        "v": 1,
        "decision_id": "srb_"
        + hashlib.sha256(json.dumps([host, job_id], separators=(",", ":")).encode()).hexdigest()[:32],
        "kind": "route.decision.baseline",
        "source": "seat-run",
        "host": host,
        "job_id": job_id,
        "source_job": record.get("job"),
        "source_record": record["source_record"],
        "ts": started or ended,
        "class": task_class,
        "class_source": "record"
        if first_value([record, request, options, receipt], "class", "klass") is not None
        else "seat_chain"
        if task_class
        else None,
        "class_candidates": candidates,
        "seat": seat,
        "model": model,
        "model_alias": first_value(identity, "model_alias"),
        "effort": first_value(identity, "worker_effort", "effort"),
        "caller": first_value([record, request, options], "caller", "caller_lane", "lane"),
        "caller_host": record.get("caller_host"),
        "caller_lane": record.get("caller_lane"),
        "box": first_value([receipt, record], "node", "box"),
        "started_at": started,
        "started_at_source": start_source if started else None,
        "ended_at": ended,
        "ended_at_source": end_source if ended else None,
        "exit_status": first_value([receipt, final, record], "exit_status", "exit_code", "rc", "code"),
        "final_code": final.get("code"),
        "attempt": 1,
        "parent_id": None,
        "arm": "explicit",
        "arm_id": "seat-run-baseline",
        "policy_rev": "seat-run-historical",
        "eligible_arms": [],
    }
    for key in (
        "status",
        "outcome",
        "verdict",
        "ok",
        "seconds",
        "wall_s",
        "tokens_in",
        "tokens_out",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_read",
        "total_tokens",
    ):
        value = first_value([record, final, receipt, handoff], key)
        if value is not None:
            row[key] = value
    if handoff:
        row["handoff"] = handoff
    return row


def source_records(directory: str, host: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    remote = host not in (
        None,
        "Studio",
        "studio",
        "localhost",
        socket.gethostname(),
        socket.gethostname().split(".")[0],
    )
    if remote:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "--",
            str(host),
            "python3 -c " + shlex.quote(SOURCE_SCRIPT) + " " + shlex.quote(directory),
        ]
    else:
        command = [sys.executable, "-c", SOURCE_SCRIPT, directory]
    result = subprocess.run(command, capture_output=True, text=True, timeout=210)
    if result.returncode:
        raise ValueError(f"seat-run scan failed (exit {result.returncode})")
    records = []
    summary = None
    for line in result.stdout.splitlines():
        item = json.loads(line)
        if "record" in item:
            records.append(item["record"])
        elif "summary" in item:
            summary = item["summary"]
    if summary is None:
        raise ValueError("seat-run scan returned no completion summary")
    return records, summary


def append_baseline(path: Path, rows: list[dict[str, Any]], dry_run: bool) -> tuple[int, int]:
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
    if dry_run and not path.exists():
        seen = set()
        for row in rows:
            seen.add(row["decision_id"])
        return len(seen), len(rows) - len(seen)
    with path.open("r" if dry_run else "a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_SH if dry_run else fcntl.LOCK_EX)
        stream.seek(0)
        seen = set()
        for line in stream:
            if line.strip():
                seen.add(json.loads(line)["decision_id"])
        fresh = []
        for row in rows:
            if row["decision_id"] not in seen:
                fresh.append(row)
                seen.add(row["decision_id"])
        if not dry_run and fresh:
            stream.seek(0, os.SEEK_END)
            stream.write("".join(json.dumps(row, sort_keys=True) + "\n" for row in fresh))
            stream.flush()
            os.fsync(stream.fileno())
        return len(fresh), len(rows) - len(fresh)


def baseline_outcomes(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "decision_ids": [row["decision_id"]],
            "worker_model": row.get("model"),
            "worker_effort_requested": row.get("effort"),
            "status": row.get("status", "unknown"),
            "outcome": row.get("outcome", row.get("status", "unknown")),
            "success_rule": "historical_seat_run_status_only",
            "attribution_complete": False,
            "cost_known": False,
            "cost_usd": None,
        }
        for row in decisions
    ]


def command_import_seat_run(args: argparse.Namespace) -> int:
    host = args.host or socket.gethostname().split(".")[0]
    host = "Studio" if host.lower() in ("studio", "localhost", "alexs-mac-studio") else host
    path = Path(args.out).expanduser() if args.out else baseline_path()
    protected = [decisions_path(), Path.home() / ".claude/logs/route-decisions.jsonl"]
    if path.name == "route-decisions.jsonl" or any(
        path.resolve() == target.resolve() or (path.exists() and target.exists() and path.samefile(target))
        for target in protected
    ):
        print("route: baseline must not write route-decisions.jsonl", file=sys.stderr)
        return 2
    source = Path(args.source_dir).expanduser().resolve()
    jobs = (Path.home() / ".agent-rails/jobs/seat-run").resolve()
    if path.resolve().is_relative_to(jobs) or path.resolve().is_relative_to(source):
        print("route: baseline output must be outside the read-only job source", file=sys.stderr)
        return 2
    try:
        records, summary = source_records(args.source_dir, args.host)
        table = load_table()
        rows = [baseline_row(record, host, table) for record in records]
        new, existing = append_baseline(path, rows, args.dry_run)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("route: seat-run import failed; check source, SSH access and baseline JSONL", file=sys.stderr)
        return 2
    result = {
        "host": host,
        "from": args.source_dir,
        "out": str(path),
        "dry_run": args.dry_run,
        "records": len(rows),
        "new_rows": new,
        "existing_rows": existing,
        **summary,
    }
    print(
        json.dumps(result, sort_keys=True)
        if args.json
        else f"{host}: {len(rows)} records, {new} new, {existing} existing, {summary['invalid']} invalid -> {path}"
    )
    return 0
