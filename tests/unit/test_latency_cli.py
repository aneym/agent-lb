from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.cli import _parse_args
from app.latency_cli import run

pytestmark = pytest.mark.unit


def _stamp(when: datetime) -> str:
    return when.astimezone(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _seed(path: str) -> None:
    now = datetime.now(UTC)
    recent = now - timedelta(hours=1)
    earlier = now - timedelta(hours=2)
    stale = now - timedelta(hours=48)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE request_logs (
            provider TEXT,
            account_id TEXT,
            requested_at TEXT,
            latency_ms INTEGER,
            latency_first_token_ms INTEGER,
            error_code TEXT,
            deleted_at TEXT
        )
        """
    )
    rows = [
        ("openai", "abcdefghijklmn", _stamp(recent), 1000, 100, None, None),
        ("openai", "abcdefghijklmn", _stamp(recent), 3000, 300, None, None),
        ("openai", "abcdefghijklmn", _stamp(recent), 5000, None, None, None),
        ("openai", "abcdefghijklmn", _stamp(recent), None, None, "upload_admission_timeout", None),
        ("anthropic", "zzzzzzzz9999", _stamp(earlier), 800, 80, None, None),
        ("anthropic", "zzzzzzzz9999", _stamp(earlier), 900, 120, None, None),
        ("anthropic", "zzzzzzzz9999", _stamp(recent), None, None, "upload_admission_rejected", None),
        ("openai", "abcdefghijklmn", _stamp(stale), 9999, 9999, None, None),
    ]
    connection.executemany(
        """
        INSERT INTO request_logs (
            provider, account_id, requested_at, latency_ms, latency_first_token_ms, error_code, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()


def test_latency_json_reports_percentiles_per_provider(tmp_path, capsys) -> None:
    database = tmp_path / "latency.db"
    _seed(str(database))
    args = _parse_args(["latency", "--json", "--db", f"sqlite:///{database}", "--since", "24h"])

    run(args)

    report = json.loads(capsys.readouterr().out)
    by_provider = {row["provider"]: row for row in report["tables"]["provider"]}
    assert by_provider["openai"]["n_rows"] == 4
    assert by_provider["openai"]["n_with_ttft"] == 2
    assert by_provider["openai"]["p50_ttft_ms"] == 200
    assert by_provider["openai"]["p95_ttft_ms"] == 300
    assert by_provider["openai"]["p50_latency_ms"] == 3000
    assert by_provider["anthropic"]["n_rows"] == 3
    assert by_provider["anthropic"]["n_with_ttft"] == 2
    assert by_provider["anthropic"]["p50_ttft_ms"] == 100
    assert by_provider["anthropic"]["p95_ttft_ms"] == 120
    assert by_provider["anthropic"]["p50_latency_ms"] == 850
    assert report["upload_admission_timeout"] == 1
    assert report["upload_admission_rejected"] == 1
    assert {row["account"] for row in report["tables"]["account"]} == {"abcdefgh", "zzzzzzzz"}
    assert all(row["hour"].endswith("Z") for row in report["tables"]["hour"])


def test_latency_without_db_reads_the_service_database(tmp_path, monkeypatch, capsys) -> None:
    import plistlib

    from app.core.config.settings import get_settings

    database = tmp_path / "service.db"
    _seed(str(database))
    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    with (agents / "com.aneyman.agent-lb.plist").open("wb") as stream:
        plistlib.dump({"EnvironmentVariables": {"AGENT_LB_DATABASE_URL": f"sqlite:///{database}"}}, stream)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_LB_DATABASE_URL")
    get_settings.cache_clear()
    try:
        run(_parse_args(["latency", "--json", "--since", "24h", "--by", "provider"]))
    finally:
        get_settings.cache_clear()

    report = json.loads(capsys.readouterr().out)
    assert {row["provider"]: row["n_rows"] for row in report["tables"]["provider"]} == {"openai": 4, "anthropic": 3}
