#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DB = "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb"
PRIMARY_URL_FILE = Path.home() / ".agent-lb/primary-database.url"
RESET_JUMP_SECONDS = 600
DROP_TOLERANCE = 0.5
COHORT_COLUMNS = {
    "session": "coalesce(client_session_id, session_id)",
    "lane": "lane",
    "seat": "caller_seat",
    "machine": "caller_machine",
    "model": "model",
    "job": "job_id",
}


def dsn() -> str:
    try:
        url = os.environ.get("AGENT_LB_DATABASE_URL") or PRIMARY_URL_FILE.read_text().strip() or DEFAULT_DB
    except OSError:
        url = DEFAULT_DB
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql_json(sql: str) -> list[dict]:
    wrapped = f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t"
    env = {**os.environ, "PGOPTIONS": "-c statement_timeout=20s"}
    result = subprocess.run(
        ["psql", dsn(), "-X", "-At", "-v", "ON_ERROR_STOP=1", "-c", wrapped],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        sys.exit(f"psql failed: {result.stderr.strip()}")
    return json.loads(result.stdout or "[]")


def range_sql(args: argparse.Namespace, column: str) -> str:
    if args.since:
        upper = f" and {column} <= {literal(args.until)}::timestamptz at time zone 'UTC'" if args.until else ""
        return f"{column} >= {literal(args.since)}::timestamptz at time zone 'UTC'{upper}"
    return f"{column} >= (now() at time zone 'UTC') - make_interval(hours => {int(args.hours)})"


def consumption_sql(args: argparse.Namespace) -> str:
    filters = [range_sql(args, "recorded_at")]
    if args.account:
        filters.append(f"account_id = {literal(args.account)}")
    if args.window:
        filters.append(f"coalesce(\"window\", 'primary') = {literal(args.window)}")
    return f"""
with s as (
  select account_id, coalesce("window", 'primary') as w, id, recorded_at, used_percent, reset_at,
    lag(used_percent) over p as prev_used, lag(reset_at) over p as prev_reset, lag(recorded_at) over p as prev_at
  from usage_history where {" and ".join(filters)}
  window p as (partition by account_id, coalesce("window", 'primary') order by recorded_at, id)
), e as (
  select *, sum(case when prev_reset is not null and reset_at - prev_reset > {RESET_JUMP_SECONDS} then 1 else 0 end)
    over (partition by account_id, w order by recorded_at, id) as epoch,
    (prev_used is not null and used_percent < prev_used - {DROP_TOLERANCE}
      and not (prev_reset is not null and reset_at - prev_reset > {RESET_JUMP_SECONDS})) as dropped
  from s
), g as (
  select account_id, w, epoch, max(used_percent) as peak,
    (array_agg(used_percent order by recorded_at, id))[1] as first_used,
    count(*) filter (where dropped) as drops, count(*) as n,
    max(extract(epoch from recorded_at - prev_at)) as max_gap_s, min(recorded_at) as start_at, max(recorded_at) as end_at
  from e group by account_id, w, epoch
)
select g.account_id, a.provider, a.plan_type, g.w as "window",
  round(sum(case when g.epoch = 0 then g.peak - g.first_used else g.peak end)::numeric, 2) as used_delta_pct,
  count(*) - 1 as resets, sum(g.drops) as drops, sum(g.n) as snapshots,
  min(g.start_at) as first_at, max(g.end_at) as last_at,
  round(extract(epoch from (now() at time zone 'UTC') - max(g.end_at))::numeric) as stale_s,
  round(max(g.max_gap_s)::numeric) as max_gap_s
from g left join accounts a on a.id = g.account_id
group by g.account_id, a.provider, a.plan_type, g.w
order by used_delta_pct desc nulls last
"""


def activity_sql(args: argparse.Namespace, accounts: list[str]) -> str:
    cohort = "false"
    if args.cohort:
        kind, _, value = args.cohort.partition("=")
        if kind not in COHORT_COLUMNS or not value:
            sys.exit(f"--cohort takes one of {sorted(COHORT_COLUMNS)} as kind=value")
        cohort = f"{COHORT_COLUMNS[kind]} like {literal(value)}"
    listed = ", ".join(literal(account) for account in accounts) or "null"
    return f"""
select account_id,
  count(distinct coalesce(request_id, id::text)) filter (where status = 'success') as requests,
  count(distinct coalesce(request_id, id::text)) filter (where status = 'success' and {cohort}) as cohort_requests,
  count(*) filter (where status <> 'success') as error_requests,
  count(*) as attempts
from request_logs where {range_sql(args, "requested_at")} and deleted_at is null and account_id in ({listed})
group by account_id
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measured quota consumption per account and window from usage_history snapshots, with optional request-cohort association."
    )
    parser.add_argument("--hours", type=float, default=48)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--account")
    parser.add_argument("--window", help="primary (5h), secondary (7d), monthly")
    parser.add_argument("--provider")
    parser.add_argument(
        "--cohort", help="kind=value with %% wildcards, kind one of session, lane, seat, machine, model, job"
    )
    args = parser.parse_args()
    started = time.monotonic()
    rows = psql_json(consumption_sql(args))
    if args.provider:
        rows = [row for row in rows if row.get("provider") == args.provider]
    activity = {
        row["account_id"]: row for row in psql_json(activity_sql(args, sorted({row["account_id"] for row in rows})))
    }
    for row in rows:
        seen = activity.get(row["account_id"], {})
        row["requests"] = seen.get("requests", 0)
        row["error_requests"] = seen.get("error_requests", 0)
        row["attempts"] = seen.get("attempts", 0)
        if args.cohort:
            row["cohort_requests"] = seen.get("cohort_requests", 0)
            share = row["cohort_requests"] / row["requests"] if row["requests"] else 0
            row["cohort_request_share"] = round(share, 4)
            row["cohort_associated_pct"] = round(float(row["used_delta_pct"] or 0) * share, 2)
    print(
        json.dumps(
            {
                "schema": "quota-consumption/1",
                "range": {"hours": args.hours} if not args.since else {"since": args.since, "until": args.until},
                "method": f"Per account and window: within each reset epoch (reset_at jumps more than {RESET_JUMP_SECONDS} s), peak used_percent minus the first reading; the first epoch in range starts from its first reading, later epochs from 0. drops counts readings more than {DROP_TOLERANCE} points below the previous one with no reset. cohort_associated_pct is the account delta times the cohort's request share on that account: an association under concurrency, not per-request causal quota.",
                "seconds": round(time.monotonic() - started, 2),
                "rows": rows,
            },
            default=str,
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
