from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

DEFAULT_DB = "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb"
PRIMARY_URL_FILE = Path.home() / ".agent-lb/primary-database.url"


def default_db() -> str:
    try:
        return PRIMARY_URL_FILE.read_text().strip() or DEFAULT_DB
    except OSError:
        return DEFAULT_DB


PSQL = shutil.which("psql") or next(
    (p for p in ("/opt/homebrew/bin/psql", "/usr/local/bin/psql") if os.access(p, os.X_OK)), "psql"
)


def utc(value: str) -> datetime:
    value = value.strip()
    duration = re.fullmatch(r"(\d+)([hd])", value)
    if duration:
        return datetime.now(timezone.utc) - timedelta(**{{"h": "hours", "d": "days"}[duration[2]]: int(duration[1])})
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def sql_time(value: datetime) -> str:
    return "'" + value.replace(tzinfo=None).isoformat(sep=" ") + "'"


def query(sql: str) -> list[list[str]]:
    dsn = os.environ.get("AGENT_LB_DATABASE_URL") or default_db()
    dsn = re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)
    result = subprocess.run(
        [PSQL, "-X", "-At", "-F|", "-v", "ON_ERROR_STOP=1", "-c", sql, dsn],
        text=True,
        capture_output=True,
        timeout=90,
    )
    if result.returncode:
        errors = [line for line in result.stderr.splitlines() if line.startswith("ERROR:")]
        raise RuntimeError(
            "database query failed (psql exit %s): %s"
            % (result.returncode, errors[0] if errors else "connection or query error")
        )
    return [line.split("|") for line in result.stdout.splitlines() if line]


def quota_receipts(start: datetime, end: datetime) -> list[dict]:
    rows = query(f"""
        WITH receipts AS (
            SELECT id, account_id,
                   coalesce((to_jsonb(r)->>'quota_observed_at')::timestamp, requested_at) AS ts,
                   caller_user, caller_machine, caller_seat, room, client_session_id, session_id,
                   input_tokens, cache_creation_tokens, output_tokens,
                   unified_5h_utilization, unified_7d_utilization,
                   to_jsonb(r)->'unified_headers' AS unified_headers
            FROM request_logs r
            WHERE provider = 'anthropic' AND account_id IS NOT NULL AND deleted_at IS NULL
              AND request_kind <> 'frame'
        ), baselines AS (
            SELECT DISTINCT ON (account_id, quota_window) account_id, ts
            FROM receipts CROSS JOIN LATERAL (
                VALUES ('5h', unified_5h_utilization), ('7d', unified_7d_utilization)
            ) AS reading(quota_window, utilization)
            WHERE ts < {sql_time(start)} AND utilization IS NOT NULL
            ORDER BY account_id, quota_window, ts DESC, id DESC
        ), floors AS (
            SELECT account_id, min(ts) AS ts FROM baselines GROUP BY account_id
        )
        SELECT row_to_json(r)::text FROM receipts r LEFT JOIN floors f USING (account_id)
        WHERE r.ts >= least(coalesce(f.ts, {sql_time(start)}), {sql_time(start)})
          AND r.ts < {sql_time(end)}
        ORDER BY r.ts, r.id
    """)
    return [json.loads("|".join(row)) for row in rows]


def quota_report(receipts: list[dict], start: datetime, end: datetime) -> dict:
    accounts = {}
    first_reading = None
    for receipt in sorted(receipts, key=lambda r: (utc(r["ts"]), r["id"])):
        ts = utc(receipt["ts"])
        if ts >= end:
            continue
        account = accounts.setdefault(receipt["account_id"], [])
        account.append(receipt)
        if any(receipt.get(f"unified_{w}_utilization") is not None for w in ("5h", "7d")):
            first_reading = min(first_reading, ts) if first_reading else ts
    shares = {}
    coverage = {
        w: {
            "readings": 0,
            "intervals": 0,
            "skipped_resets_or_drops": 0,
            "token_intervals": 0,
            "request_share_intervals": 0,
            "unmeasured_requests": 0,
        }
        for w in ("5h", "7d")
    }
    for account_id, rows in accounts.items():
        for window in ("5h", "7d"):
            prior = None
            pending = []
            c = coverage[window]
            for row in rows:
                pending.append(row)
                value = row.get(f"unified_{window}_utilization")
                if value is None:
                    continue
                ts = utc(row["ts"])
                if ts >= start:
                    c["readings"] += 1
                if prior is not None:
                    before = prior[f"unified_{window}_utilization"]
                    old_reset = (prior.get("unified_headers") or {}).get(f"anthropic-ratelimit-unified-{window}-reset")
                    new_reset = (row.get("unified_headers") or {}).get(f"anthropic-ratelimit-unified-{window}-reset")
                    crossed_reset = old_reset is not None and new_reset is not None and old_reset != new_reset

                    too_long = (ts - utc(prior["ts"])).total_seconds() >= (18000 if window == "5h" else 604800)
                    if ts >= start:
                        if value < before or crossed_reset or too_long:
                            c["skipped_resets_or_drops"] += 1
                            c["unmeasured_requests"] += sum(utc(r["ts"]) >= start for r in pending)
                        else:
                            c["intervals"] += 1
                            weights = [
                                r["input_tokens"] + (r.get("cache_creation_tokens") or 0) + r["output_tokens"]
                                if r.get("input_tokens") is not None and r.get("output_tokens") is not None
                                else None
                                for r in pending
                            ]
                            token_method = all(w is not None for w in weights) and sum(w or 0 for w in weights) > 0
                            weights = cast(list[float], weights) if token_method else [1] * len(pending)
                            c["token_intervals" if token_method else "request_share_intervals"] += 1
                            total = sum(weights)
                            for r, weight in zip(pending, weights):
                                if utc(r["ts"]) < start:
                                    continue

                                caller = {k: r.get(k) for k in ("caller_user", "caller_machine", "caller_seat", "room")}
                                caller["session"] = (
                                    r.get("client_session_id") or r.get("session_id")
                                    if not (caller["caller_seat"] or caller["room"])
                                    else None
                                )
                                key = (account_id, *caller.values())
                                share = shares.setdefault(
                                    key, {"account": account_id, **caller, "percent_5h": 0.0, "percent_7d": 0.0}
                                )
                                share[f"percent_{window}"] += (value - before) * 100 * weight / total
                elif ts >= start:
                    c["unmeasured_requests"] += sum(utc(r["ts"]) >= start for r in pending)
                prior = row
                pending = []
            c["unmeasured_requests"] += sum(utc(r["ts"]) >= start for r in pending)
    consumers = {}
    for share in shares.values():
        key = tuple(share[k] for k in ("caller_user", "caller_machine", "caller_seat", "room", "session"))
        consumer = consumers.setdefault(
            key, {k: share[k] for k in ("caller_user", "caller_machine", "caller_seat", "room", "session")}
        )
        for window in ("5h", "7d"):
            field = f"percent_{window}"
            consumer[field] = consumer.get(field, 0.0) + share[field]
    rank = lambda r: (-r["percent_7d"], -r["percent_5h"])
    return {
        "since": start.isoformat(),
        "until": end.isoformat(),
        "history_existed": first_reading is not None,
        "has_pre_window_baseline": first_reading is not None and first_reading < start,
        "first_reading": first_reading.isoformat() if first_reading else None,
        "method": "Positive consecutive utilization deltas per account, weighted by uncached input "
        "(including cache creation) + output tokens; equal request shares if any tokens are missing "
        "or total weight is zero. Reset changes, drops and full-window gaps excluded. "
        "Attribution is an estimate; external account traffic and rounding cannot be separated. "
        "Top five sum account percentage points, ranked by 7d then 5h.",
        "coverage": coverage,
        "accounts": sorted(shares.values(), key=rank),
        "top5": sorted(consumers.values(), key=rank)[:5],
    }


def quota_display(data: dict) -> str:
    lines = [
        f"Claude quota  {data['since']} to {data['until']}",
        data["method"],
        "account              caller (user / machine / seat / room / session)                       %5h       %7d",
    ]

    def line(row: dict, account: str) -> str:
        caller = "/".join(
            str(row.get(k) or "-") for k in ("caller_user", "caller_machine", "caller_seat", "room", "session")
        )
        return f"{account:20} {caller:55} {row['percent_5h']:9.4f} {row['percent_7d']:9.4f}"

    lines.extend(line(row, row["account"]) for row in data["accounts"])
    lines.append("Top 5 consumers (sum of account percentage points):")
    lines.extend(line(row, "all accounts") for row in data["top5"])
    if not data["accounts"]:
        lines.append("No attributable quota intervals. Collection needs consecutive readings.")
    lines.append("Coverage: " + json.dumps(data["coverage"]))
    return "\n".join(lines)
