from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.audit_quota import quota_allocation
from app.audit_token_output import html_report, text_report
from app.audit_token_report import DIMS, add_quota_units, build_report
from app.audit_transcripts import RULE_PATH, metadata, pane_maps, safe_label, session_agents, session_index
from app.core.prompt_tokens import SESSION_KEY_SQL


def add_parser(subparsers):
    audit = subparsers.add_parser("audit", help="Read-only receipt audits.")
    commands = audit.add_subparsers(dest="audit_command", required=True)
    tokens = commands.add_parser("tokens", help="Account for tokens and list-price dollars.")
    tokens.add_argument("--window", default="7d")
    tokens.add_argument("--since")
    tokens.add_argument("--until")
    tokens.add_argument("--by", default="provider,account,purpose,lane,seat,model")
    output = tokens.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--html", action="store_true")
    tokens.add_argument("--out", type=Path)
    tokens.add_argument("--db")
    tokens.add_argument("--top", type=int, default=30)
    tokens.add_argument("--snapshot-panes-only", action="store_true")


class AuditUsageError(ValueError):
    pass


def window(args):
    def parse(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    until = parse(args.until) if args.until else datetime.now(UTC)
    match = re.fullmatch(r"([1-9]\d*)([mhd])", args.window)
    if not match:
        raise ValueError("--window must be a positive number followed by m, h or d")
    if args.since:
        relative = re.fullmatch(r"([1-9]\d*)([mhd])", args.since)
        try:
            since = (
                until - timedelta(minutes=int(relative[1]) * {"m": 1, "h": 60, "d": 1440}[relative[2]])
                if relative
                else parse(args.since)
            )
        except (ValueError, OverflowError):
            raise AuditUsageError("--since takes an ISO time or <n>m/<n>h/<n>d") from None
    else:
        since = until - timedelta(minutes=int(match[1]) * {"m": 1, "h": 60, "d": 1440}[match[2]])
    if since >= until:
        raise ValueError("--since must precede --until")
    return since, until


QUERY = f"""
WITH base AS (
 SELECT id, account_id, provider, model, reasoning_effort, useragent_group, source, request_kind,
 service_tier, input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens, cache_read_tokens,
 reasoning_tokens, cost_usd, status, error_code, upstream_status_code, upstream_error_code, requested_at, request_id,
 caller_seat, ATTRIBUTION_COLUMNS,
 coalesce({SESSION_KEY_SQL},'receipt:' || id::text) AS sid
 FROM request_logs WHERE deleted_at IS NULL AND requested_at >= %s AND requested_at < %s
)
SELECT *, 1 AS requests,
 CASE WHEN request_id IS NULL THEN 1
 ELSE row_number() OVER (PARTITION BY request_id ORDER BY requested_at,id) END AS retry_index
FROM base ORDER BY requested_at,id
"""


@contextmanager
def connect(args, notes):
    import psycopg
    from psycopg.rows import dict_row

    from app.core.config.settings import get_settings

    url = args.db or os.environ.get("AGENT_LB_DATABASE_URL") or get_settings().database_url
    if not args.db and url.startswith("sqlite"):
        from app.core.config.service_database import LOCAL_DATABASE_URL, primary_database_url

        notes.append("Configured SQLite default has no live receipts; using the primary PostgreSQL audit source.")
        url = primary_database_url() or LOCAL_DATABASE_URL
    url = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(
        url, options="-c default_transaction_read_only=on -c statement_timeout=90000", row_factory=dict_row
    ) as connection:
        yield connection


def load_receipts(args, since, until, notes):
    with connect(args, notes) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,alias FROM accounts")
            aliases = {r["id"]: safe_label(r["alias"], r["id"][:8]) for r in cursor.fetchall()}
            cursor.execute(
                """SELECT DISTINCT ON (account_id) account_id,used_percent,recorded_at,reset_at
            FROM usage_history WHERE (window_minutes=10080 OR (provider='anthropic' AND "window"='secondary'))
            AND recorded_at <= %s AND reset_at > %s ORDER BY account_id,recorded_at DESC""",
                (until.replace(tzinfo=None), int(until.timestamp())),
            )
            weekly = {
                aliases.get(r["account_id"], r["account_id"][:8]): {
                    "used_percent": r["used_percent"],
                    "recorded_at": r["recorded_at"].isoformat(),
                    "reset_at": r["reset_at"],
                }
                for r in cursor.fetchall()
            }
            cursor.execute(
                """WITH quota AS (
                SELECT id,account_id,provider,"window",window_minutes,used_percent,recorded_at,reset_at,
                CASE WHEN provider='anthropic' AND "window"='secondary' THEN 'weekly'
                WHEN provider='openai' AND window_minutes=10080 AND "window"='primary' THEN 'weekly'
                ELSE '5h' END AS quota_window
                FROM usage_history WHERE recorded_at >= %s AND recorded_at <= %s
                AND used_percent IS NOT NULL AND reset_at IS NOT NULL
                AND ((provider='anthropic' AND "window"='secondary')
                OR (provider='openai' AND window_minutes=10080 AND "window"='primary')
                OR (provider='anthropic' AND window_minutes=300 AND "window"='primary'))
                ), changes AS (
                SELECT *, lag(used_percent) OVER run AS prior_used, lag(reset_at) OVER run AS prior_reset
                FROM quota WINDOW run AS (PARTITION BY provider,account_id,quota_window ORDER BY recorded_at,id)
                )
                SELECT account_id,provider,"window",window_minutes,used_percent,recorded_at,reset_at FROM changes
                WHERE recorded_at >= %s OR prior_used IS NULL OR prior_used <> used_percent OR prior_reset <> reset_at
                ORDER BY provider,account_id,recorded_at""",
                (
                    (since - timedelta(days=14)).replace(tzinfo=None),
                    until.replace(tzinfo=None),
                    since.replace(tzinfo=None),
                ),
            )
            snapshots = cursor.fetchall()
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='request_logs'")
            present = {r["column_name"] for r in cursor.fetchall()}
            columns = ", ".join(
                [
                    *(name if name in present else f"NULL AS {name}" for name in ("lane", "job_id")),
                    "coalesce(dispatch_id, unified_headers->>'x-agent-lb-dispatch') AS dispatch_id"
                    if "dispatch_id" in present
                    else "unified_headers->>'x-agent-lb-dispatch' AS dispatch_id",
                ]
            )
            cursor.execute(
                QUERY.replace("ATTRIBUTION_COLUMNS", columns).encode(),
                (since.replace(tzinfo=None), until.replace(tzinfo=None)),
            )
            rows = cursor.fetchall()
            cursor.execute(
                f"""SELECT DISTINCT {SESSION_KEY_SQL} AS sid
                FROM request_logs WHERE deleted_at IS NULL AND requested_at >= %s AND requested_at < %s""".encode(),
                ((since - timedelta(hours=1)).replace(tzinfo=None), since.replace(tzinfo=None)),
            )
            preexisting = {r["sid"] for r in cursor.fetchall()}
    return rows, snapshots, aliases, weekly, preexisting


def run(args):
    try:
        since, until = window(args)
        dimensions = args.by.split(",")
        for dim in dimensions:
            if dim not in DIMS:
                raise AuditUsageError(f"unknown --by dimension {dim!r}; known: {', '.join(DIMS)}")
        if args.top < 1:
            raise AuditUsageError("--top must be positive")
        panes, notes = pane_maps()
        if args.snapshot_panes_only:
            print("Pane snapshot complete. " + " ".join(notes))
            return
        rows, snapshots, aliases, weekly, preexisting = load_receipts(args, since, until, notes)
        quota_rows, quota = quota_allocation(rows, snapshots, since, until)
        for row, points in zip(rows, quota_rows, strict=True):
            row.update(quota_points=points["weekly"], quota_5h_points=points["5h"])
        for index, item in enumerate(quota["unattributed"]):
            rows.append(
                {
                    "sid": f"unattributed-quota:{index}",
                    "provider": item["provider"],
                    "account_id": item["account_id"],
                    "model": "unknown",
                    "requests": 0,
                    "cost_usd": 0.0,
                    "requested_at": until.replace(tzinfo=None),
                    "status": "success",
                    "quota_points": item["points"] if item["window"] == "weekly" else 0.0,
                    "quota_5h_points": item["points"] if item["window"] == "5h" else 0.0,
                }
            )
        index = session_index()
        metas = {}
        for sid in {row["sid"] for row in rows}:
            meta = metadata(index.get(sid))
            if sid in panes:
                meta.update(pane_title=panes[sid]["title"], pane=panes[sid]["pane"])
            metas[sid] = meta
        with RULE_PATH.open("rb") as stream:
            rules = tomllib.load(stream)["rules"]
        agents = {
            sid: session_agents(index.get(sid), since, until)
            for sid in {row["sid"] for row in rows if row["sid"] in index}
        }
        report = build_report(rows, aliases, weekly, metas, rules, dimensions, args.top, agents, preexisting)
        add_quota_units(report, quota)
        report.update(
            window={"since": since.isoformat(), "until": until.isoformat()},
            generated_at=datetime.now(UTC).isoformat(),
            notes=notes
            + [
                "Rolling UTC window, not account reset windows; weekly quota snapshots are separate.",
                "Reasoning is reported but not added to output. Missing usage is not estimated.",
                "Observed cost splits use current model/tier proportions; unknown splits unallocated.",
                "Waste assignments are exclusive candidates, not proven savings; cache USD is premium over reads.",
                "LB totals are allocated by transcript input-side cost, not transcript output.",
                "No transcript prompt text is emitted. Unknown purpose remains explicit.",
            ],
        )
        if args.json:
            output = json.dumps(report, indent=2) + "\n"
        elif args.html:
            output = html_report(report)
        else:
            output = text_report(report)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output)
            print(f"Wrote token audit: {args.out}")
        else:
            print(output, end="")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:
        message = f"Token audit failed ({type(exc).__name__}); check local inputs and database availability."
        raise SystemExit(message) from None
