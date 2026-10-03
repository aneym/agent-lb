"""Read-only TTFT report over request_logs. Works on PostgreSQL and SQLite."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median

DIMENSIONS = ("provider", "account", "hour")
ADMISSION_CODES = ("upload_admission_timeout", "upload_admission_rejected")


def add_parser(subparsers) -> None:
    latency = subparsers.add_parser("latency", help="Read-only TTFT and latency report.")
    latency.add_argument("--since", default="24h", help="Window start: Nh, Nd, or an ISO timestamp.")
    latency.add_argument(
        "--by",
        default="provider,account,hour",
        help="Comma-separated tables: provider, account, hour.",
    )
    latency.add_argument("--json", action="store_true")
    latency.add_argument("--db", help="Database URL (default: AGENT_LB_DATABASE_URL or settings).")


def parse_since(value: str, *, until: datetime) -> datetime:
    match = re.fullmatch(r"([1-9]\d*)([hd])", value)
    if match:
        hours = int(match[1]) * (24 if match[2] == "d" else 1)
        return until - timedelta(hours=hours)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("--since must be Nh, Nd, or an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    if parsed >= until:
        raise SystemExit("--since must precede now")
    return parsed


def database_url(args) -> str:
    url = args.db or os.environ.get("AGENT_LB_DATABASE_URL")
    if url:
        return url
    from app.core.config.settings import get_settings

    return get_settings().database_url


def _normalize_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")


def _sqlite_path(url: str) -> str:
    bare = url.split("?", 1)[0]
    marker = ":///"
    if marker not in bare:
        raise SystemExit("SQLite URL must include a file path")
    return bare.split(marker, 1)[1]


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _hour_label(value: datetime | str) -> str:
    hour = _as_utc(value).replace(minute=0, second=0, microsecond=0)
    return hour.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(0.95 * len(ordered))
    return ordered[rank - 1]


def _p50(values: list[int]) -> int | None:
    if not values:
        return None
    return int(round(median(values)))


def _group_key(dimension: str, row: dict) -> str:
    if dimension == "provider":
        provider = row.get("provider")
        return str(provider) if provider else "unknown"
    if dimension == "account":
        account_id = row.get("account_id")
        if not account_id:
            return "unknown"
        return str(account_id)[:8]
    return _hour_label(row["requested_at"])


def _summarize(rows: list[dict]) -> dict:
    ttft = [int(row["latency_first_token_ms"]) for row in rows if row.get("latency_first_token_ms") is not None]
    latency = [int(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    return {
        "n_rows": len(rows),
        "n_with_ttft": len(ttft),
        "p50_ttft_ms": _p50(ttft),
        "p95_ttft_ms": _p95(ttft),
        "p50_latency_ms": _p50(latency),
    }


def build_report(rows: list[dict], *, since: datetime, until: datetime, dimensions: list[str]) -> dict:
    admission = {code: 0 for code in ADMISSION_CODES}
    for row in rows:
        code = row.get("error_code")
        if code in admission:
            admission[code] += 1
    tables: dict[str, list[dict]] = {}
    for dimension in dimensions:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            grouped[_group_key(dimension, row)].append(row)
        tables[dimension] = [{dimension: key, **_summarize(grouped[key])} for key in sorted(grouped)]
    return {
        "since": _stamp(since),
        "until": _stamp(until),
        "upload_admission_timeout": admission["upload_admission_timeout"],
        "upload_admission_rejected": admission["upload_admission_rejected"],
        "tables": tables,
    }


def _load_rows(url: str, since: datetime, until: datetime) -> list[dict]:
    sql = """
        SELECT provider, account_id, requested_at, latency_ms, latency_first_token_ms, error_code
        FROM request_logs
        WHERE deleted_at IS NULL AND requested_at >= ? AND requested_at < ?
    """
    since_naive = since.astimezone(UTC).replace(tzinfo=None)
    until_naive = until.astimezone(UTC).replace(tzinfo=None)
    normalized = _normalize_url(url)
    if normalized.startswith("sqlite"):
        path = _sqlite_path(normalized.replace("sqlite+aiosqlite", "sqlite", 1))
        uri = f"file:{path}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise SystemExit(f"database connection failed: {type(exc).__name__}") from exc
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.execute(
                sql,
                (since_naive.strftime("%Y-%m-%d %H:%M:%S"), until_naive.strftime("%Y-%m-%d %H:%M:%S")),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    if not normalized.startswith("postgresql"):
        raise SystemExit("database URL must be PostgreSQL or SQLite")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise SystemExit("psycopg is required to read PostgreSQL") from exc
    postgres_sql = sql.replace("?", "%s")
    try:
        with psycopg.connect(
            normalized,
            options="-c default_transaction_read_only=on -c statement_timeout=90000",
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(postgres_sql, (since_naive, until_naive))
                return list(cursor.fetchall())
    except Exception as exc:
        raise SystemExit(f"database connection failed: {type(exc).__name__}") from exc


def _cell(value: object) -> str:
    if value is None:
        return "-"
    return str(value)


def format_text(report: dict) -> str:
    lines: list[str] = []
    for dimension, rows in report["tables"].items():
        header = (
            f"{dimension:<12} {'n_rows':>12} {'n_with_ttft':>12} "
            f"{'p50_ttft_ms':>12} {'p95_ttft_ms':>12} {'p50_latency_ms':>15}"
        )
        lines.append(header)
        for row in rows:
            lines.append(
                f"{_cell(row[dimension]):<12} {row['n_rows']:>12} {row['n_with_ttft']:>12} "
                f"{_cell(row['p50_ttft_ms']):>12} {_cell(row['p95_ttft_ms']):>12} "
                f"{_cell(row['p50_latency_ms']):>15}"
            )
        lines.append("")
    lines.append(
        "upload_admission_timeout="
        f"{report['upload_admission_timeout']} "
        "upload_admission_rejected="
        f"{report['upload_admission_rejected']}"
    )
    return "\n".join(lines)


def run(args) -> None:
    dimensions = [part.strip() for part in args.by.split(",") if part.strip()]
    if not dimensions or any(part not in DIMENSIONS for part in dimensions):
        raise SystemExit("--by must be a subset of provider,account,hour")
    until = datetime.now(UTC)
    since = parse_since(args.since, until=until)
    rows = _load_rows(database_url(args), since, until)
    report = build_report(rows, since=since, until=until, dimensions=dimensions)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(format_text(report))
