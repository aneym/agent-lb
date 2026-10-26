from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.audit_tokens import connect, window

JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:/-]{0,63}")
TOKENS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "reasoning_tokens",
)
QUERY = """
SELECT job_id, lane, caller_seat, dispatch_id, requested_at, latency_ms, latency_first_token_ms,
 model, provider, status, input_tokens, output_tokens, cached_input_tokens, cache_read_tokens,
 cache_creation_tokens, reasoning_tokens, cost_usd
FROM request_logs
WHERE deleted_at IS NULL AND requested_at >= %s AND requested_at < %s AND job_id = ANY(%s)
ORDER BY job_id, requested_at, id
"""


def add_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("jobs", help="Audit recorded tokens and list-price cost by job.")
    parser.set_defaults(window="7d")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--job", action="append", required=True)
    parser.add_argument("--calls", type=int, default=400)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--db")


def iso(stamp: datetime) -> str:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def report(rows, since, until, limit):
    jobs: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row["job_id"]
        end = iso(row["requested_at"])
        if name not in jobs:
            jobs[name] = {
                "job_id": name,
                "lanes": {},
                "seats": {},
                "dispatches": {},
                "requests": 0,
                **dict.fromkeys(TOKENS, 0),
                "cost_usd": 0.0,
                "first_at": end,
                "last_at": end,
                "models": {},
                "providers": {},
                "calls": [],
                "calls_omitted": 0,
            }
        job = jobs[name]
        job["requests"] += 1
        job["last_at"] = end
        for key in TOKENS:
            job[key] += row[key] or 0
        job["cost_usd"] += float(row["cost_usd"] or 0)
        for field, counts in (
            ("model", "models"),
            ("provider", "providers"),
            ("lane", "lanes"),
            ("caller_seat", "seats"),
            ("dispatch_id", "dispatches"),
        ):
            value = row[field]
            if value is not None:
                job[counts][value] = job[counts].get(value, 0) + 1
        if len(job["calls"]) >= limit:
            job["calls_omitted"] += 1
            continue
        job["calls"].append(
            {
                "end_at": end,
                "lane": row["lane"],
                "seat": row["caller_seat"],
                "dispatch_id": row["dispatch_id"],
                "latency_ms": row["latency_ms"],
                "first_token_ms": row["latency_first_token_ms"],
                "model": row["model"],
                "provider": row["provider"],
                "status": row["status"],
                **{key: row[key] for key in TOKENS},
                "cost_usd": float(row["cost_usd"]) if row["cost_usd"] is not None else None,
            }
        )
    return {"schema": 1, "window": {"since": iso(since), "until": iso(until)}, "jobs": list(jobs.values())}


def run(args):
    try:
        if len(args.job) > 200 or any(JOB_ID.fullmatch(job) is None for job in args.job):
            raise ValueError("--job requires at most 200 valid job ids (1–64 characters)")
        if args.calls < 0:
            raise ValueError("--calls must be nonnegative")
        since, until = window(args)
    except (ValueError, OverflowError) as exc:
        print(f"usage: agent-lb audit jobs: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        with connect(args, []) as connection:
            with connection.cursor() as cursor:
                cursor.execute(QUERY, (since.replace(tzinfo=None), until.replace(tzinfo=None), args.job))
                result = report(cursor, since, until, args.calls)
        output = json.dumps(result) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output)
            print(f"Wrote jobs audit: {args.out}")
        else:
            print(output, end="")
    except Exception as exc:
        print(f"audit jobs: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
