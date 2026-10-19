from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from app.modules.usage.sqlite_history import (
    UsageHistorySnapshot,
    _bulk_history_fingerprint_sqlite,
    _bulk_history_since_sqlite,
    _clear_bulk_history_since_sqlite_cache,
    _fingerprint_grouped_history,
)

pytestmark = pytest.mark.integration


def test_bulk_history_since_sqlite_cache_reuses_superset_and_picks_up_appends(tmp_path):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
                (3, "acc2", 30.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1", "acc2"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert [row.id for row in first["acc1"]] == [1, 2]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (4, "acc1", 40.0, "2026-01-01 00:02:00", 1000.0, 10080, "secondary"),
        )
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1", "acc2"],
        "secondary",
        datetime(2026, 1, 1, 0, 1, 0),
    )
    assert [row.id for row in second["acc1"]] == [2, 4]
    assert [row.id for row in second["acc2"]] == [3]

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_since_sqlite_cache_rebuilds_after_delete_and_id_reuse(tmp_path):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert [row.used_percent for row in first["acc1"]] == [10.0, 20.0]

    with sqlite3.connect(db_path) as conn:
        conn.execute("delete from usage_history")
        conn.execute(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "acc1", 75.0, "2026-01-01 00:02:00", 2000.0, 10080, "secondary"),
        )
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.id for row in second["acc1"]] == [1]
    assert [row.used_percent for row in second["acc1"]] == [75.0]

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_since_sqlite_cache_rebuilds_after_offsetting_row_corrections(tmp_path):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert [row.used_percent for row in first["acc1"]] == [10.0, 20.0]

    with sqlite3.connect(db_path) as conn:
        conn.execute("update usage_history set used_percent = 15.0 where id = 1")
        conn.execute("update usage_history set used_percent = 15.0 where id = 2")
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.used_percent for row in second["acc1"]] == [15.0, 15.0]

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_sqlite_fingerprint_normalizes_recorded_at_text(tmp_path):
    db_path = tmp_path / "usage.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.execute(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "acc1", 10.0, "2026-01-01 00:00:00.000000", 1000.0, 10080, "secondary"),
        )
        conn.commit()

    grouped = {
        "acc1": [
            UsageHistorySnapshot(
                id=1,
                account_id="acc1",
                used_percent=10.0,
                recorded_at=datetime(2026, 1, 1, 0, 0, 0),
                reset_at=1000.0,
                window_minutes=10080,
            )
        ]
    }

    with sqlite3.connect(db_path) as conn:
        sqlite_fingerprint = _bulk_history_fingerprint_sqlite(
            conn,
            ["acc1"],
            "secondary",
            datetime(2026, 1, 1, 0, 0, 0),
        )

    assert sqlite_fingerprint == _fingerprint_grouped_history(grouped)
