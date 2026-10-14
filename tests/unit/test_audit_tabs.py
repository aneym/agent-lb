

import json
import os
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine

from app.db.models import Base


@pytest.mark.parametrize(
    "window_args",
    [
        ["--window", "24h", "--until", "2026-10-11T00:00:00Z"],
        ["--since", "2026-10-10T00:00:00Z", "--until", "2026-10-11T02:00:00+02:00"],
    ],
)
def test_tabs_cli_receipts_and_exports(tmp_path, window_args):
    agents = tmp_path / "agents"
    for tab, pane in (("cos", "w5H:p15W"), ("factory", "w5H:p16W")):
        directory = agents / tab
        directory.mkdir(parents=True)
        (directory / "agent.json").write_text(json.dumps({"pane": pane, "model": "fable", "effort": "high"}))
    snapshots = tmp_path / ".agent-lb/audit/pane-snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / "2026-10-10.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "session": session,
                    "pane": pane,
                    "title": tab,
                }
            )
            for session, pane, tab in (("one", "w5H:p15W", "cos"), ("two", "w5H:p16W", "factory"))
        )
    )

    db = tmp_path / "receipts.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with sqlite3.connect(db) as connection:
        for ident, session, model, stamp, deleted in (
            (1, "one", "claude-fable-5-1", "2026-10-10 10:00:00", None),
            (2, "one", "claude-fable-5-1", "2026-10-10 11:00:00", None),
            (3, "one", "claude-opus-5-5", "2026-10-10 12:00:00", None),
            (4, "two", "claude-opus-5-5", "2026-10-10 13:00:00", None),
            (5, "one", "claude-fable-5-1", "2026-10-11 00:00:00", None),
            (6, "one", "claude-fable-5-1", "2026-10-10 14:00:00", "2026-10-10 15:00:00"),
            (7, "unmapped", "claude-fable-5-1", "2026-10-10 14:00:00", None),
        ):
            connection.execute(
                "INSERT INTO request_logs (id,request_id,session_id,provider,model,requested_at,deleted_at,"
                "status,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,cost_usd) "
                "VALUES (?,?,?,'anthropic',?,?,?,'success',100,20,1000,200,1.0)",
                (ident, f"request-{ident}", session, model, stamp, deleted),
            )
        connection.execute("UPDATE request_logs SET account_id='a'")
        connection.executemany(
            'INSERT INTO usage_history (account_id,provider,"window",window_minutes,used_percent,recorded_at,reset_at) '
            "VALUES ('a','anthropic','secondary',10080,?,?,1792281600)",
            [(0, "2026-10-10 00:00:00"), (10, "2026-10-11 00:00:00")],
        )
    env = dict(os.environ, HOME=str(tmp_path), PATH="/usr/bin:/bin")
    output = tmp_path / "audit.jsonl"
    report = tmp_path / "latest.md"
    command = [
        sys.executable,
        "-m",
        "app.cli",
        "audit",
        "tabs",
        *window_args,
        "--db",
        f"sqlite:///{db}",
        "--agents-dir",
        str(agents),
        "--tabs",
        "cos,factory",
        "--out-jsonl",
        str(output),
        "--out-md",
        str(report),
        "--json",
    ]
    first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    rows = json.loads(first.stdout)
    assert [row["tab"] for row in rows] == ["cos", "factory"]
    assert rows[0]["requests"] == 3
    assert rows[0]["models"] == {"claude-fable-5-1": 2, "claude-opus-5-5": 1}
    assert rows[0]["dominant_model"] == "claude-fable-5-1"
    assert rows[0]["ok_model"] is True
    assert rows[1]["requests"] == 1
    assert rows[1]["models"] == {"claude-opus-5-5": 1}
    assert rows[1]["ok_model"] is False
    assert rows[0]["fresh_input"] == 300
    assert rows[0]["cache_read"] == 3000
    assert rows[0]["cache_write"] == 600
    assert rows[0]["output"] == 60
    assert rows[0]["usd"] == 3
    assert rows[0]["points"] == pytest.approx(6)
    assert rows[1]["points"] == pytest.approx(2)
    assert rows[0]["cache_write_share_of_input_cost"] == pytest.approx(
        (2 * 2.5 / 5.5 + 1 / 2) / (2 * 4.5 / 5.5 + 1.6 / 2)
    )
    assert rows[0]["date"] == "2026-10-11"
    assert rows[0]["window_start"] == "2026-10-10T00:00:00+00:00"
    assert rows[0]["window_end"] == "2026-10-11T00:00:00+00:00"
    assert [json.loads(line) for line in output.read_text().splitlines()] == rows
    assert "| Total |" in report.read_text()
    assert "Model mismatches: factory." in report.read_text()
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM request_logs WHERE session_id='two'")
    second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    empty = json.loads(second.stdout)[1]
    assert empty["requests"] == 0
    assert empty["models"] == {}
    assert empty["dominant_model"] is None
    assert empty["ok_model"] is False
    assert empty["note"]
    assert all(empty[key] == 0 for key in ("fresh_input", "cache_read", "cache_write", "output", "points", "usd"))
    assert len(output.read_text().splitlines()) == 4
