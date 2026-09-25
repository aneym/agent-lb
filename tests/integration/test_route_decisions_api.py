"""Contract for the read-only dashboard ledger projection."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_route_decisions_pairing_filters_counts_and_redaction(async_client, monkeypatch, tmp_path):
    # A response should represent the independently written ledger without claiming
    # that an indistinguishable pair of concurrent dispatches both completed.
    ledger = tmp_path / "dispatch.jsonl"
    monkeypatch.setenv("ROUTE_LEDGER", str(ledger))
    rows = [
        {"event": "dispatch", "ts": "2026-01-01T23:00:00Z", "session_id": "old", "subagent_type": "seat-a"},
        {
            "event": "dispatch",
            "ts": "2026-01-02T00:00:00Z",
            "session_id": "s1",
            "task_class": "implement",
            "subagent_type": "seat-a",
            "model": "model-a",
            "prompt_sha256": "one",
            "name": "named",
            "prompt": "PRIVATE PROMPT TEXT",
        },
        {
            "event": "closeout",
            "ts": "2026-01-02T00:01:00Z",
            "session_id": "s1",
            "prompt_sha256": "one",
            "name": "named",
            "subagent_type": "seat-a",
            "ok": True,
            "duration_s": 42.5,
            "tokens_in": 123,
            "tokens_out": 45,
        },
        {
            "event": "dispatch",
            "ts": "2026-01-02T00:02:00Z",
            "session_id": "s2",
            "task_class": "explore",
            "subagent_type": "seat-b",
            "model": "model-b",
            "prompt_sha256": "open",
        },
        {
            "event": "dispatch",
            "ts": "2026-01-02T00:03:00Z",
            "session_id": "s3",
            "task_class": "implement",
            "subagent_type": "seat-a",
            "model": "model-a",
            "prompt_sha256": "duplicate",
        },
        {
            "event": "dispatch",
            "ts": "2026-01-02T00:04:00Z",
            "session_id": "s3",
            "task_class": "implement",
            "subagent_type": "seat-a",
            "model": "model-a",
            "prompt_sha256": "duplicate",
        },
        {
            "event": "closeout",
            "ts": "2026-01-02T00:05:00Z",
            "session_id": "s3",
            "prompt_sha256": "duplicate",
            "subagent_type": "seat-a",
            "ok": True,
        },
        {
            "event": "dispatch",
            "ts": "2026-01-02T00:06:00Z",
            "session_id": "s4",
            "task_class": "implement",
            "subagent_type": "seat-c",
            "model": "model-c",
            "prompt_sha256": "failure",
        },
        {
            "event": "closeout",
            "ts": "2026-01-02T00:07:00Z",
            "session_id": "s4",
            "prompt_sha256": "failure",
            "subagent_type": "seat-c",
            "ok": False,
            "error": "PRIVATE PROMPT TEXT",
        },
        {
            "event": "of_decision",
            "ts": "2026-01-02T00:08:00Z",
            "session_id": "s5",
            "decision_id": "dec-5",
            "task_class": "implement",
            "candidates": [{"id": "seat-a"}],
            "abstain": "static_decider",
            "pick": None,
            "fallback": "route_pick",
            "seat": "seat-a",
            "model": "model-a",
            "validation": None,
            "decider_ms": 19,
            "decider_usd": 0.003,
            "policy_version": 2,
            "task_head": "PRIVATE PROMPT TEXT",
        },
        {
            "event": "of_decision",
            "ts": "2026-01-03T00:00:00Z",
            "session_id": "future",
            "decision_id": "future",
            "seat": "seat-a",
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(row) for row in rows[:4])
        + "\n{broken json\n"
        + "\n".join(json.dumps(row) for row in rows[4:])
        + "\n"
    )
    params = {"since": "2026-01-02T00:00:00Z", "until": "2026-01-03T00:00:00Z"}
    response = await async_client.get("/api/route-decisions", params=params)
    assert response.status_code == 200
    data = response.json()
    assert data["counts"] == {"total": 6, "fallbacks": 1, "noPick": 1, "failed": 1}
    assert data["ledgerPath"] == str(ledger)
    assert data["truncated"] is False
    assert [d["outcome"]["state"] for d in data["decisions"]] == ["ok", "failed", "open", "open", "open", "ok"]
    assert [d["outcome"]["match"] for d in data["decisions"]] == [
        "none",
        "exact",
        "ambiguous",
        "ambiguous",
        "none",
        "exact",
    ]
    paired = data["decisions"][-1]
    assert (paired["outcome"]["durationS"], paired["outcome"]["tokensIn"], paired["outcome"]["tokensOut"]) == (
        42.5,
        123,
        45,
    )
    assert data["decisions"][0]["candidates"] == [
        {"seat": "seat-a", "model": None, "score": None, "excludedReason": None}
    ]
    assert data["decisions"][0]["abstained"] is True
    assert "PRIVATE PROMPT TEXT" not in response.text

    filtered = await async_client.get(
        "/api/route-decisions", params={**params, "class": "implement", "outcome": "open", "limit": 1}
    )
    assert filtered.status_code == 200
    assert filtered.json()["counts"] == {"total": 2, "fallbacks": 0, "noPick": 0, "failed": 0}
    assert len(filtered.json()["decisions"]) == 1
    no_pick = await async_client.get("/api/route-decisions", params={**params, "outcome": "no_pick"})
    assert no_pick.json()["counts"] == {"total": 1, "fallbacks": 1, "noPick": 1, "failed": 0}
    assert no_pick.json()["decisions"][0]["id"] == "dec-5"
