"""Waste attribution contracts at the transcript-to-report boundary."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.audit_tokens import build_report, session_agents

START = datetime(2026, 9, 29, 10, tzinfo=UTC)


def report_for(
    tmp_path, monkeypatch, calls, writes=None, boundaries=(), with_transcript=True, gaps=None,
    boundary_type="system",
):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".claude" / "projects" / "session.jsonl"
    path.parent.mkdir(parents=True)
    rows, lines = [], []
    for index, inputs in enumerate(calls):
        stamp = START + timedelta(seconds=index * 10 + (gaps or {}).get(index, 0))
        write = (writes or {}).get(index, 0)
        rows.append(
            dict(
                sid="session", account_id="account", provider="anthropic", model="claude-opus-5-5",
                requested_at=stamp, requests=1, status="success", input_tokens=1000,
                output_tokens=100, cache_creation_tokens=write, cache_read_tokens=0,
                cost_usd=1, quota_points=2, quota_5h_points=1, useragent_group="claude-cli",
            )
        )
        if index in boundaries:
            lines.append({
                **({"type": "system", "subtype": "compact_boundary"}
                   if boundary_type == "system" else {"type": "user", "isCompactSummary": True}),
                "timestamp": (stamp - timedelta(seconds=1)).isoformat(),
            })
        lines.append({
            "type": "assistant", "timestamp": stamp.isoformat(),
            "message": {
                "id": f"message-{index}", "model": "claude-opus-5-5",
                "usage": {"input_tokens": 1000, "output_tokens": 100, "cache_creation_input_tokens": write},
                "content": [
                    {"type": "tool_use", "id": f"tool-{index}-{j}", "name": "Bash", "input": value}
                    for j, value in enumerate(inputs)
                ],
            },
        })
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    agents = session_agents(path, START, START + timedelta(days=1)) if with_transcript else []
    return build_report(rows, {}, {}, {}, [], ["purpose"], 10, agents_by_session={"session": agents})


@pytest.mark.parametrize("boundary_type", ["system", "summary"])
def test_compact_boundary_owns_next_cache_write(tmp_path, monkeypatch, boundary_type):
    report = report_for(
        tmp_path, monkeypatch, [[], [], []], writes={0: 100000, 1: 100000, 2: 100000},
        boundaries=(1,), boundary_type=boundary_type,
    )
    assert report["waste"]["compaction"]["requests"] == 1
    assert report["waste"]["compaction"]["quota_points"] > 0
    assert report["waste"]["bust"]["requests"] == 1
    assert report["waste"]["other_miss"]["requests"] == 0


@pytest.mark.parametrize("repeats,expected", [(19, False), (20, True)])
def test_identical_tools_in_fifty_call_window(tmp_path, monkeypatch, repeats, expected):
    # Different JSON key order is still the same tool input.
    calls = [
        [{"command": "status", "timeout": 1} if i % 2 else {"timeout": 1, "command": "status"}]
        if i < repeats else [{"command": f"distinct-{i}"}]
        for i in range(50)
    ]
    report = report_for(tmp_path, monkeypatch, calls)
    assert report["waste"]["looping"]["requests"] == (50 if expected else 0)
    assert report["waste"]["looping"]["usd"] == (50 if expected else 0)
    assert report["waste"]["looping"]["quota_points"] == (100 if expected else 0)


def test_distinct_low_output_calls_are_coordinator_not_looping(tmp_path, monkeypatch):
    report = report_for(tmp_path, monkeypatch, [[{"command": f"status-{i}"}] for i in range(1000)])
    assert report["waste"]["looping"]["requests"] == 0
    assert report["waste"]["coordinator"]["requests"] == 1000
    assert report["waste"]["coordinator"]["usd"] == 1000
    assert report["waste"]["coordinator"]["quota_points"] == 2000
    assert report["waste_total_usd"] == 1000


@pytest.mark.parametrize("with_transcript", [False, True])
def test_daily_cache_premiums_reconcile_quota(tmp_path, monkeypatch, with_transcript):
    report = report_for(
        tmp_path, monkeypatch, [[], [], []], writes={0: 100000, 1: 100000, 2: 100000},
        with_transcript=with_transcript, gaps={2: 400},
    )
    for kind in ("bust", "ttl_expiry"):
        entries = [day[kind] for day in report["cache_premium_by_day"].values()]
        assert sum(entry["usd"] for entry in entries) == pytest.approx(report["waste"][kind]["usd"])
        assert sum(entry["quota_points"] for entry in entries) == pytest.approx(report["waste"][kind]["quota_points"])
        assert report["waste"][kind]["quota_points"] > 0
