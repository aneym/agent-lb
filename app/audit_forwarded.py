"""Compare requested values with logged forwarded values, not upstream confirmation."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def arguments(parser):
    parser.add_argument("--caller-seat", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort")
    parser.add_argument("--since", help="ISO timestamp (default: 24 hours ago).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ledger", action="store_true")


def add_parser(commands):
    arguments(commands.add_parser("forwarded", help=__doc__))


def database_url():
    from app.core.config.settings import get_settings

    url = os.environ.get("AGENT_LB_DATABASE_URL") or get_settings().database_url
    if url.startswith("sqlite"):
        plist = Path.home() / "Library/LaunchAgents/com.aneyman.agent-lb.plist"
        if plist.is_file():
            with plist.open("rb") as stream:
                url = plistlib.load(stream).get("EnvironmentVariables", {}).get("AGENT_LB_DATABASE_URL", "")
    if not url or not url.startswith(("postgresql://", "postgresql+asyncpg://", "postgresql+psycopg://")):
        raise ValueError("Live PostgreSQL database URL is not resolvable; configure AGENT_LB_DATABASE_URL.")
    return url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")


def resolved_model(model):
    from app.modules.proxy.request_policy import resolve_model_alias

    if "-latest" in model:
        result = subprocess.run(
            ["route", "resolve", model], capture_output=True, text=True, timeout=30, check=True
        )
        model = result.stdout.strip()
        if not model or len(model.split()) != 1:
            raise ValueError("Model alias could not be resolved.")
    return resolve_model_alias(model)


def run(args):
    try:
        since = datetime.now(UTC) - timedelta(hours=24)
        if args.since:
            try:
                since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("--since must be an ISO timestamp.") from None
            since = since.replace(tzinfo=UTC) if since.tzinfo is None else since.astimezone(UTC)
        model = resolved_model(args.model)
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            database_url(), connect_timeout=10, row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=90000",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT model, reasoning_effort AS effort, count(*) AS count
                    FROM request_logs WHERE caller_seat = %s AND requested_at >= %s
                    AND deleted_at IS NULL GROUP BY model, reasoning_effort
                    ORDER BY model, reasoning_effort""",
                    (args.caller_seat, since.replace(tzinfo=None)),
                )
                forwarded = cursor.fetchall()
        rows = sum(row["count"] for row in forwarded)
        outcome = "unknown" if not rows else (
            "match" if all(row["model"] == model and (args.effort is None or row["effort"] == args.effort)
                           for row in forwarded) else "mismatch"
        )
        report = dict(label="requested_vs_forwarded", caller_seat=args.caller_seat,
                      requested=dict(model=model, effort=args.effort), forwarded=forwarded, rows=rows, outcome=outcome)
        if args.ledger:
            ledger = Path(os.environ.get("ROUTE_LEDGER", "~/.claude/logs/dispatch.jsonl")).expanduser()
            ledger.parent.mkdir(parents=True, exist_ok=True)
            event = {key: report[key] for key in ("caller_seat", "requested", "forwarded", "outcome")}
            with ledger.open("a") as stream:
                entry = dict(ts=datetime.now(UTC).isoformat(), event="forwarded_check", **event)
                stream.write(json.dumps(entry) + "\n")
        if args.json:
            print(json.dumps(report))
        else:
            print(f"requested_vs_forwarded: {args.caller_seat}: {outcome} ({rows} rows)")
            print(f"requested: {model} effort={args.effort or 'unspecified'}")
            for row in forwarded:
                print(f"forwarded: {row['model']} effort={row['effort']} count={row['count']}")
    except ValueError as exc:
        # Only our validation messages are safe; never display database or settings exceptions.
        message = str(exc)
        safe = ("Live PostgreSQL database URL is not resolvable; configure AGENT_LB_DATABASE_URL.",
                "Model alias could not be resolved.", "--since must be an ISO timestamp.")
        print(message if message in safe else "Forwarded audit failed; check configuration.", file=sys.stderr)
        raise SystemExit(3) from None
    except Exception:
        print("Forwarded audit failed; check database availability and model alias resolution.", file=sys.stderr)
        raise SystemExit(3) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
