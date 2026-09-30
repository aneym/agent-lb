"""Daily audit output contracts: counts, revert horizon and evidence-gated moves."""
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scripts.routing_daily_audit import dispatch, proposals, store, summarize, write_outputs


@pytest.fixture
def observations(tmp_path):
    start = datetime(2026, 9, 28, tzinfo=UTC)
    events, verdicts, merges = [], [], []
    for model, seconds in (("sol-medium", 600), ("grok-low", 500)):
        for index in range(6):
            pr = index + (10 if model == "sol-medium" else 20)
            sid = str(pr)
            event = dict(ts=(start + timedelta(hours=index)).isoformat(), session_id=sid,
                         source="seat", name="implementer", task_class="implement", model=model,
                         cwd=f"/work/{pr}", pr=pr, vendor="cursor" if model == "grok-low" else "openai")
            events.extend([event, event | dict(ok=True, wall_s=seconds, tokens_in=100, tokens_out=20)])
            verdicts.append(dict(pr=pr, ts=(start + timedelta(hours=index, minutes=15)).isoformat(),
                                 **{"pass": True}))
            merges.append(dict(number=pr, mergedAt=(start + timedelta(hours=index, minutes=20)).isoformat()))
    # One observed redo in a distinct session; it is not another dispatch closeout.
    events.append(events[0] | dict(session_id="redo", ts=(start + timedelta(minutes=30)).isoformat(),
                                  ok=False, wall_s=60, pr=None))
    verdicts.insert(0, dict(pr=10, ts=(start + timedelta(minutes=10)).isoformat(), **{"pass": False}))
    review = dict(ts=(start + timedelta(minutes=10)).isoformat(), session_id="review", name="verifier",
                  source="seat", task_class="review", model="sonnet", pr=10, ok=True, wall_s=60)
    events.append(review)
    log = tmp_path / "dispatch.jsonl"
    log.write_text("\n".join(json.dumps(event) for event in events))
    db = tmp_path / "store.db"
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE request_logs (requested_at TEXT, session_id TEXT, account_id TEXT, "
                           "provider TEXT, model TEXT, input_tokens INT, output_tokens INT)")
        connection.execute("INSERT INTO request_logs VALUES (?, '10', 'a', 'openai', 'sol-medium', 100, 20)",
                           ((start + timedelta(minutes=2)).isoformat(),))
        connection.execute("CREATE TABLE usage_history (account_id TEXT, provider TEXT, window TEXT, "
                           "recorded_at TEXT, used_percent REAL, reset_at INT)")
        connection.executemany("INSERT INTO usage_history VALUES ('a','openai','secondary',?,?,123)",
                               [(start.isoformat(), 1), ((start + timedelta(minutes=5)).isoformat(), 3)])
        connection.commit()
        errors = []
        requests, usage = store(db, start, start + timedelta(days=3), errors)
        assert not errors
    runs = dispatch(log, start, start + timedelta(days=3), errors)
    assert not errors
    return start, runs, verdicts, merges, requests, usage


def test_routing_daily_audit_counts_and_published_report(observations, tmp_path):
    start, runs, verdicts, merges, requests, usage = observations
    rows = summarize(runs, verdicts, merges, [], requests, usage, start, start + timedelta(days=1))
    sol = next(row for row in rows if row["model"] == "sol-medium")
    assert (sol["runs"], sol["finished"], sol["accepted"], sol["redone"], sol["fix_rounds"]) == (7, 6, 6, 0, 1)
    assert sol["openai_weekly_points_per_finished"] == pytest.approx(2 / 6)
    assert next(row for row in rows if row["model"] == "sonnet")["review_fails"] == 1
    assert next(row for row in rows if row["model"] == "grok-low")["cursor_envelope_tokens"] == 720
    report = dict(until=(start + timedelta(days=1)).isoformat(), line="fixture audit", rows=rows,
                  proposals=["no change"], notes=[], errors=[])
    page = tmp_path / "page.html"
    write_outputs(report, tmp_path / "audit", page)
    assert json.loads((tmp_path / "audit/2026-09-29.json").read_text())["rows"] == rows
    assert "fixture audit" in page.read_text()
    assert "_themes/agent-rails.css" in page.read_text()


@pytest.mark.parametrize("hours,expected", [(47, 1), (48, 1), (49, 0)])
def test_routing_daily_audit_revert_48h(observations, hours, expected):
    start, runs, verdicts, merges, requests, usage = observations
    merged = datetime.fromisoformat(merges[0]["mergedAt"])
    reverts = [dict(pr=10, ts=(merged + timedelta(hours=hours)).isoformat())]
    rows = summarize(runs, verdicts, merges, reverts, requests, usage, start, start + timedelta(days=3))
    assert next(row for row in rows if row["model"] == "sol-medium")["reverted_48h"] == expected


def test_routing_daily_audit_proposes_only_with_accepted_evidence(observations):
    start, runs, verdicts, merges, requests, usage = observations
    rows = summarize(runs, verdicts, merges, [], requests, usage, start, start + timedelta(days=7))
    ladder = {"implement": ["sol-medium", "grok-low"]}
    assert proposals(rows, ladder)[0].startswith("propose: move grok-low above sol-medium")
    no_verdicts = summarize(runs, [], merges, [], requests, usage, start, start + timedelta(days=7))
    assert proposals(no_verdicts, ladder) == ["no change"]


def test_routing_daily_audit_quantized_claude_points_show_fleet_estimate(observations):
    start, runs, verdicts, merges, requests, usage = observations
    # Spend precedes the first sample: adjacent integer snapshots cannot attribute
    # its points, but the fleet's later weekly delta supplies an estimate.
    requests.append(dict(requested_at=(start + timedelta(minutes=1)).isoformat(), session_id="review",
                         account_id="claude", provider="anthropic", model="sonnet", cost_usd=2,
                         input_tokens=100, output_tokens=10))
    requests.append(dict(requested_at=(start + timedelta(minutes=20)).isoformat(), session_id="fleet",
                         account_id="claude", provider="anthropic", model="opus", cost_usd=8,
                         input_tokens=100, output_tokens=10))
    usage.extend([
        dict(account_id="claude", provider="anthropic", window="secondary", reset_at=123,
             recorded_at=(start + timedelta(minutes=5)).isoformat(), used_percent=10),
        dict(account_id="claude", provider="anthropic", window="secondary", reset_at=123,
             recorded_at=(start + timedelta(minutes=30)).isoformat(), used_percent=15),
    ])
    usage.sort(key=lambda row: row["recorded_at"])
    rows = summarize(runs, verdicts, merges, [], requests, usage, start, start + timedelta(days=1))
    row = next(row for row in rows if row["model"] == "sonnet")
    assert row["anthropic_spend_usd"] == 2
    assert row["claude_weekly_points_status"] == "quantized"
    assert row["claude_weekly_points"] is None
    assert row["claude_weekly_points_per_finished"] is None
    assert row["claude_weekly_points_fleet_estimate"] == 1
