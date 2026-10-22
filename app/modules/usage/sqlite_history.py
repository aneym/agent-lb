from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from threading import RLock

from app.db.usage_models import UsageHistory


@dataclass(frozen=True, slots=True)
class UsageHistorySnapshot:
    id: int
    account_id: str
    used_percent: float
    recorded_at: datetime
    reset_at: float | None
    window_minutes: int | None


@dataclass(slots=True)
class _BulkHistoryCacheEntry:
    since: datetime
    max_id: int
    fingerprint: _BulkHistoryFingerprint
    rows_by_account: dict[str, list[UsageHistorySnapshot]]


@dataclass(frozen=True, slots=True)
class _BulkHistoryFingerprint:
    row_count: int
    max_id: int
    id_sum: int
    used_percent_total: float
    reset_at_total: float
    window_minutes_sum: int
    max_recorded_at: str
    content_digest: str


_BULK_HISTORY_SQLITE_CACHE: dict[tuple[str, tuple[str, ...], str], _BulkHistoryCacheEntry] = {}


_BULK_HISTORY_SQLITE_CACHE_LOCK = RLock()


def _clear_bulk_history_since_sqlite_cache() -> None:
    with _BULK_HISTORY_SQLITE_CACHE_LOCK:
        _BULK_HISTORY_SQLITE_CACHE.clear()


def _bulk_history_cache_key(
    db_path: str,
    account_ids: list[str],
    window: str,
) -> tuple[str, tuple[str, ...], str]:
    return (db_path, tuple(sorted(account_ids)), window)


def _clone_filtered_history(
    grouped: dict[str, list[UsageHistorySnapshot]],
    since: datetime,
) -> dict[str, list[UsageHistorySnapshot]]:
    filtered_grouped: dict[str, list[UsageHistorySnapshot]] = {}
    for account_id, rows in grouped.items():
        filtered = [row for row in rows if row.recorded_at >= since]
        if filtered:
            filtered_grouped[account_id] = filtered
    return filtered_grouped


def _max_snapshot_id(grouped: dict[str, list[UsageHistorySnapshot]]) -> int:
    return max((row.id for rows in grouped.values() for row in rows), default=0)


def _fingerprint_grouped_history(grouped: dict[str, list[UsageHistorySnapshot]]) -> _BulkHistoryFingerprint:
    row_count = 0
    max_id = 0
    id_sum = 0
    used_percent_total = 0.0
    reset_at_total = 0.0
    window_minutes_sum = 0
    max_recorded_at = ""
    digest = sha256()
    for account_id, rows in sorted(grouped.items()):
        for row in rows:
            row_count += 1
            max_id = max(max_id, row.id)
            id_sum += row.id
            used_percent_total += row.used_percent
            reset_at_total += row.reset_at or 0.0
            window_minutes_sum += row.window_minutes or 0
            recorded_at = row.recorded_at.isoformat(sep=" ")
            max_recorded_at = max(max_recorded_at, recorded_at)
            digest.update(
                repr(
                    (
                        account_id,
                        row.id,
                        row.used_percent,
                        recorded_at,
                        row.reset_at,
                        row.window_minutes,
                    )
                ).encode("utf-8")
            )
    return _BulkHistoryFingerprint(
        row_count=row_count,
        max_id=max_id,
        id_sum=id_sum,
        used_percent_total=used_percent_total,
        reset_at_total=reset_at_total,
        window_minutes_sum=window_minutes_sum,
        max_recorded_at=max_recorded_at,
        content_digest=digest.hexdigest(),
    )


def _append_grouped_history(
    target: dict[str, list[UsageHistorySnapshot]],
    source: dict[str, list[UsageHistorySnapshot]],
) -> None:
    for account_id, rows in source.items():
        bucket = target.setdefault(account_id, [])
        bucket.extend(rows)
        bucket.sort(key=lambda row: (row.recorded_at, row.id))


def _merged_grouped_history(
    target: dict[str, list[UsageHistorySnapshot]],
    source: dict[str, list[UsageHistorySnapshot]],
) -> dict[str, list[UsageHistorySnapshot]]:
    merged = {account_id: list(rows) for account_id, rows in target.items()}
    _append_grouped_history(merged, source)
    return merged


def _bulk_history_fingerprint_sqlite(
    conn: sqlite3.Connection,
    account_ids: list[str],
    window: str,
    since: datetime,
) -> _BulkHistoryFingerprint:
    placeholders = ",".join("?" for _ in account_ids)
    since_param = since.isoformat(sep=" ")
    if window == "primary":
        window_clause = "coalesce(window, 'primary') = 'primary'"
        params = [*account_ids, since_param]
    else:
        window_clause = "window = ?"
        params = [*account_ids, window, since_param]
    sql = f"""
        select id, account_id, used_percent, recorded_at, reset_at, window_minutes
        from usage_history
        where account_id in ({placeholders})
          and {window_clause}
          and recorded_at >= ?
        order by account_id, recorded_at asc, id asc
    """
    row_count = 0
    max_id = 0
    id_sum = 0
    used_percent_total = 0.0
    reset_at_total = 0.0
    window_minutes_sum = 0
    max_recorded_at = ""
    digest = sha256()
    for row in conn.execute(sql, params):
        row_id = int(row[0])
        account_id = str(row[1])
        used_percent = float(row[2])
        recorded_at = _parse_sqlite_datetime(row[3]).isoformat(sep=" ")
        reset_at = float(row[4]) if row[4] is not None else None
        window_minutes = int(row[5]) if row[5] is not None else None
        row_count += 1
        max_id = max(max_id, row_id)
        id_sum += row_id
        used_percent_total += used_percent
        reset_at_total += reset_at or 0.0
        window_minutes_sum += window_minutes or 0
        max_recorded_at = max(max_recorded_at, recorded_at)
        digest.update(repr((account_id, row_id, used_percent, recorded_at, reset_at, window_minutes)).encode("utf-8"))
    return _BulkHistoryFingerprint(
        row_count=row_count,
        max_id=max_id,
        id_sum=id_sum,
        used_percent_total=used_percent_total,
        reset_at_total=reset_at_total,
        window_minutes_sum=window_minutes_sum,
        max_recorded_at=max_recorded_at,
        content_digest=digest.hexdigest(),
    )


def _query_bulk_history_since_sqlite(
    conn: sqlite3.Connection,
    account_ids: list[str],
    window: str,
    since: datetime,
    *,
    after_id: int | None = None,
) -> dict[str, list[UsageHistorySnapshot]]:
    placeholders = ",".join("?" for _ in account_ids)
    since_param = since.isoformat(sep=" ")
    id_clause = ""
    params: list[object]
    if window == "primary":
        window_clause = "coalesce(window, 'primary') = 'primary'"
        params = [*account_ids, since_param]
    else:
        window_clause = "window = ?"
        params = [*account_ids, window, since_param]
    if after_id is not None:
        id_clause = "and id > ?"
        params.append(after_id)
    sql = f"""
        select id, account_id, used_percent, recorded_at, reset_at, window_minutes
        from usage_history
        where account_id in ({placeholders})
          and {window_clause}
          and recorded_at >= ?
          {id_clause}
        order by account_id, recorded_at asc
    """
    grouped: dict[str, list[UsageHistorySnapshot]] = {}
    rows = conn.execute(sql, params)
    for row in rows:
        snapshot = UsageHistorySnapshot(
            id=int(row[0]),
            account_id=str(row[1]),
            used_percent=float(row[2]),
            recorded_at=_parse_sqlite_datetime(row[3]),
            reset_at=float(row[4]) if row[4] is not None else None,
            window_minutes=int(row[5]) if row[5] is not None else None,
        )
        grouped.setdefault(snapshot.account_id, []).append(snapshot)
    return grouped


def _parse_sqlite_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _usage_history_from_sqlite_row(row) -> UsageHistory:
    return UsageHistory(
        id=int(row[0]),
        account_id=str(row[1]),
        recorded_at=_parse_sqlite_datetime(row[2]),
        window=row[3],
        used_percent=float(row[4]),
        input_tokens=int(row[5]) if row[5] is not None else None,
        output_tokens=int(row[6]) if row[6] is not None else None,
        reset_at=int(row[7]) if row[7] is not None else None,
        window_minutes=int(row[8]) if row[8] is not None else None,
        credits_has=bool(row[9]) if row[9] is not None else None,
        credits_unlimited=bool(row[10]) if row[10] is not None else None,
        credits_balance=float(row[11]) if row[11] is not None else None,
        credits_cap=float(row[12]) if len(row) > 12 and row[12] is not None else None,
        credits_spent=float(row[13]) if len(row) > 13 and row[13] is not None else None,
    )


def _latest_by_account_sqlite(
    db_path: str,
    window: str | None,
    account_ids: list[str] | None,
) -> dict[str, UsageHistory]:
    if account_ids is None:
        account_sql = "select id from accounts"
        account_params: list[object] = []
    elif not account_ids:
        return {}
    else:
        placeholders = ",".join("?" for _ in account_ids)
        account_sql = f"select id from accounts where id in ({placeholders})"
        account_params = list(account_ids)

    if not window or window == "primary":
        window_clause = "coalesce(window, 'primary') = 'primary'"
        window_params: list[object] = []
    else:
        window_clause = "window = ?"
        window_params = [window]
    latest_sql = f"""
        select id, account_id, recorded_at, window, used_percent,
               input_tokens, output_tokens, reset_at, window_minutes,
               credits_has, credits_unlimited, credits_balance,
               credits_cap, credits_spent
        from usage_history
        where account_id = ?
          and {window_clause}
        order by recorded_at desc, id desc
        limit 1
    """

    latest: dict[str, UsageHistory] = {}
    with sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        accounts = [str(row[0]) for row in conn.execute(account_sql, account_params)]
        for account_id in accounts:
            row = conn.execute(latest_sql, [account_id, *window_params]).fetchone()
            if row is not None:
                entry = _usage_history_from_sqlite_row(row)
                latest[entry.account_id] = entry
    return latest


def _bulk_history_since_sqlite(
    db_path: str,
    account_ids: list[str],
    window: str,
    since: datetime,
) -> dict[str, list[UsageHistorySnapshot]]:
    cache_key = _bulk_history_cache_key(db_path, account_ids, window)
    with sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        with _BULK_HISTORY_SQLITE_CACHE_LOCK:
            cached = _BULK_HISTORY_SQLITE_CACHE.get(cache_key)
            if cached is not None and cached.since <= since:
                current_fingerprint = _bulk_history_fingerprint_sqlite(conn, account_ids, window, cached.since)
                new_rows = _query_bulk_history_since_sqlite(
                    conn,
                    account_ids,
                    window,
                    cached.since,
                    after_id=cached.max_id,
                )
                candidate_rows = _merged_grouped_history(cached.rows_by_account, new_rows)
                candidate_fingerprint = _fingerprint_grouped_history(candidate_rows)
                if current_fingerprint != candidate_fingerprint:
                    grouped = _query_bulk_history_since_sqlite(conn, account_ids, window, cached.since)
                    cached.max_id = _max_snapshot_id(grouped)
                    cached.fingerprint = _fingerprint_grouped_history(grouped)
                    cached.rows_by_account = grouped
                    return _clone_filtered_history(grouped, since)
                if new_rows:
                    _append_grouped_history(cached.rows_by_account, new_rows)
                    cached.max_id = max(cached.max_id, _max_snapshot_id(new_rows))
                    cached.fingerprint = current_fingerprint
                return _clone_filtered_history(cached.rows_by_account, since)

            grouped = _query_bulk_history_since_sqlite(conn, account_ids, window, since)
            _BULK_HISTORY_SQLITE_CACHE[cache_key] = _BulkHistoryCacheEntry(
                since=since,
                max_id=_max_snapshot_id(grouped),
                fingerprint=_fingerprint_grouped_history(grouped),
                rows_by_account=grouped,
            )
            return _clone_filtered_history(grouped, since)
