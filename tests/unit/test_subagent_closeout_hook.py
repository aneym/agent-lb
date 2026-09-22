from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "config" / "coding-agents" / "hooks" / "subagent-closeout.py"
PROMPT = "Implement the closeout token fields."
SESSION = "session-1"

INTERPRETERS = [sys.executable]
if Path("/usr/bin/python3").exists():
    INTERPRETERS.append("/usr/bin/python3")


def _assistant(ts: str, message_id: str | None, model: str, usage: dict[str, int]) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "model": model, "usage": usage, "content": []}
    if message_id is not None:
        message["id"] = message_id
    return {"type": "assistant", "timestamp": ts, "message": message}


def _write_transcript(path: Path) -> None:
    rows = [
        {"type": "user", "timestamp": "2026-09-22T10:00:00.000Z", "message": {"role": "user", "content": PROMPT}},
        _assistant(
            "2026-09-22T10:00:10.000Z",
            "msg_a",
            "claude-opus-5-5",
            {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
        ),
        _assistant(
            "2026-09-22T10:00:20.000Z",
            "msg_a",
            "claude-opus-5-5",
            {
                "input_tokens": 10,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 3000,
                "output_tokens": 40,
            },
        ),
        {"type": "user", "timestamp": "2026-09-22T10:01:00.000Z", "message": {"role": "user", "content": "tool ok"}},
        _assistant(
            "2026-09-22T10:01:30.500Z",
            "msg_b",
            "claude-opus-5-5",
            {
                "input_tokens": 5,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 4000,
                "output_tokens": 60,
            },
        ),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _run(interpreter: str, tmp_path: Path, transcript: Path) -> list[dict[str, object]]:
    ledger = tmp_path / "dispatch.jsonl"
    dispatch = {
        "ts": "2026-09-22T10:00:00Z",
        "event": "dispatch",
        "session_id": SESSION,
        "subagent_type": "opus-seat",
        "name": "closeout-probe",
        "task_class": "implement",
        "model": "opus",
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
    }
    ledger.write_text(json.dumps(dispatch) + "\n")
    payload = {
        "session_id": SESSION,
        "agent_id": "agent-1",
        "agent_type": "closeout-probe",
        "agent_transcript_path": str(transcript),
        "last_assistant_message": "Done. All tests pass.",
    }
    subprocess.run(
        [interpreter, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        check=True,
        env={**os.environ, "DISPATCH_LEDGER": str(ledger)},
    )
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    return [record for record in records if record.get("event") == "closeout"]


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_closeout_records_transcript_cost(interpreter: str, tmp_path: Path) -> None:
    transcript = tmp_path / "subagents" / "agent-1.jsonl"
    transcript.parent.mkdir()
    _write_transcript(transcript)

    closeouts = _run(interpreter, tmp_path, transcript)

    assert len(closeouts) == 1
    closeout = closeouts[0]
    assert closeout["match"] == "prompt_hash"
    assert closeout["model"] == "claude-opus-5-5"
    assert closeout["dispatch_model"] == "opus"
    assert closeout["tokens_in"] == (10 + 200 + 3000) + (5 + 50 + 4000)
    assert closeout["tokens_out"] == 40 + 60
    assert closeout["cache_read_tokens"] == 3000 + 4000
    assert closeout["wall_s"] == 90.5


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_missing_transcript_writes_null_cost(interpreter: str, tmp_path: Path) -> None:
    closeouts = _run(interpreter, tmp_path, tmp_path / "subagents" / "missing.jsonl")

    assert len(closeouts) == 1
    closeout = closeouts[0]
    for field in ("model", "tokens_in", "tokens_out", "cache_read_tokens", "wall_s"):
        assert closeout[field] is None
    assert closeout["matched"] is True


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_identical_prompts_are_told_apart_by_name(interpreter: str, tmp_path: Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    digest = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    base = {"event": "dispatch", "session_id": SESSION, "subagent_type": "opus-seat", "prompt_sha256": digest}
    rows = [
        {**base, "ts": "2026-09-22T10:00:00Z", "name": "arm-older"},
        {**base, "ts": "2026-09-22T10:00:05Z", "name": "arm-newer"},
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    transcript = tmp_path / "subagents" / "agent-1.jsonl"
    transcript.parent.mkdir()
    _write_transcript(transcript)
    payload = {
        "session_id": SESSION,
        "agent_id": "agent-older",
        "agent_type": "arm-older",
        "agent_transcript_path": str(transcript),
        "last_assistant_message": "Done.",
    }
    subprocess.run(
        [interpreter, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        check=True,
        env={**os.environ, "DISPATCH_LEDGER": str(ledger)},
    )
    closeout = [json.loads(line) for line in ledger.read_text().splitlines()][-1]
    assert closeout["event"] == "closeout"
    assert closeout["name"] == "arm-older"


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_identical_unnamed_prompts_are_marked_ambiguous(interpreter: str, tmp_path: Path) -> None:
    ledger = tmp_path / "custom.jsonl"
    digest = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    base = {"event": "dispatch", "session_id": SESSION, "subagent_type": "opus-seat", "prompt_sha256": digest}
    ledger.write_text("".join(json.dumps({**base, "ts": f"2026-09-22T10:00:0{i}Z"}) + "\n" for i in range(2)))
    transcript = tmp_path / "subagents" / "agent-1.jsonl"
    transcript.parent.mkdir()
    _write_transcript(transcript)
    payload = {
        "session_id": SESSION,
        "agent_id": "agent-x",
        "agent_type": "opus-seat",
        "agent_transcript_path": str(transcript),
        "last_assistant_message": "Done.",
    }
    env = {key: value for key, value in os.environ.items() if key != "DISPATCH_LEDGER"}
    # ROUTE_LEDGER alone must be honoured, as seat-guard honours it.
    env["ROUTE_LEDGER"] = str(ledger)
    subprocess.run([interpreter, str(HOOK)], input=json.dumps(payload), text=True, check=True, env=env)
    subprocess.run([interpreter, str(HOOK)], input=json.dumps(payload), text=True, check=True, env=env)
    closeouts = [json.loads(line) for line in ledger.read_text().splitlines() if '"closeout"' in line]
    assert len(closeouts) == 2
    for closeout in closeouts:
        # Neither sibling's stop is pinned on a particular dispatch, and the first
        # ambiguous closeout does not consume one on replay.
        assert closeout["match"] == "prompt_hash_ambiguous"
        assert closeout["matched"] is False
        assert closeout["prompt_sha256"] is None
        assert closeout["subagent_type"] == "opus-seat"
        assert closeout["tokens_out"] == 100


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_ambiguous_siblings_on_different_seats_share_no_seat(interpreter: str, tmp_path: Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    digest = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    rows = [
        {
            "event": "dispatch",
            "session_id": SESSION,
            "ts": "2026-09-22T10:00:00Z",
            "subagent_type": "opus-seat",
            "task_class": "implement",
            "model": "opus",
            "prompt_sha256": digest,
        },
        {
            "event": "dispatch",
            "session_id": SESSION,
            "ts": "2026-09-22T10:00:01Z",
            "subagent_type": "cursor-seat",
            "task_class": "implement",
            "model": "grok",
            "prompt_sha256": digest,
        },
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    transcript = tmp_path / "subagents" / "agent-1.jsonl"
    transcript.parent.mkdir()
    _write_transcript(transcript)
    # agent_type names neither seat (a caller-given name that no dispatch recorded).
    payload = {
        "session_id": SESSION,
        "agent_id": "x",
        "agent_type": "unrecorded-name",
        "agent_transcript_path": str(transcript),
        "last_assistant_message": "Done.",
    }
    subprocess.run(
        [interpreter, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        check=True,
        env={**os.environ, "DISPATCH_LEDGER": str(ledger)},
    )
    closeout = [json.loads(line) for line in ledger.read_text().splitlines()][-1]
    assert closeout["match"] == "prompt_hash_ambiguous"
    assert closeout["task_class"] == "implement"
    assert closeout["dispatch_model"] is None
    assert closeout["subagent_type"] is None


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_replay_honours_a_historical_ambiguous_closeout_that_claimed_a_dispatch(
    interpreter: str, tmp_path: Path
) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    digest = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    base = {"event": "dispatch", "session_id": SESSION, "subagent_type": "opus-seat", "prompt_sha256": digest}
    rows = [
        {**base, "ts": "2026-09-22T10:00:00Z"},
        {**base, "ts": "2026-09-22T10:00:01Z"},
        # Written by the previous hook: ambiguous, but pinned on the newest sibling.
        {
            "event": "closeout",
            "session_id": SESSION,
            "subagent_type": "opus-seat",
            "prompt_sha256": digest,
            "match": "prompt_hash_ambiguous",
            "matched": True,
        },
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    transcript = tmp_path / "subagents" / "agent-1.jsonl"
    transcript.parent.mkdir()
    _write_transcript(transcript)
    payload = {
        "session_id": SESSION,
        "agent_id": "y",
        "agent_type": "opus-seat",
        "agent_transcript_path": str(transcript),
        "last_assistant_message": "Done.",
    }
    subprocess.run(
        [interpreter, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        check=True,
        env={**os.environ, "DISPATCH_LEDGER": str(ledger)},
    )
    closeout = [json.loads(line) for line in ledger.read_text().splitlines()][-1]
    # One sibling is still open, so this stop joins it exactly.
    assert closeout["match"] == "prompt_hash"
    assert closeout["matched"] is True


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_an_agent_type_equal_to_a_siblings_seat_does_not_fake_an_exact_join(interpreter: str, tmp_path: Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    digest = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    rows = [
        {
            "event": "dispatch",
            "session_id": SESSION,
            "ts": "2026-09-22T10:00:00Z",
            "subagent_type": "opus-seat",
            "task_class": "implement",
            "model": "opus",
            "prompt_sha256": digest,
        },
        {
            "event": "dispatch",
            "session_id": SESSION,
            "ts": "2026-09-22T10:00:01Z",
            "subagent_type": "cursor-seat",
            "task_class": "mechanical",
            "model": "grok",
            "prompt_sha256": digest,
        },
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    transcript = tmp_path / "subagents" / "agent-1.jsonl"
    transcript.parent.mkdir()
    _write_transcript(transcript)
    # agent_type is a caller name that happens to spell the other sibling's seat.
    payload = {
        "session_id": SESSION,
        "agent_id": "z",
        "agent_type": "cursor-seat",
        "agent_transcript_path": str(transcript),
        "last_assistant_message": "Done.",
    }
    subprocess.run(
        [interpreter, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        check=True,
        env={**os.environ, "DISPATCH_LEDGER": str(ledger)},
    )
    closeout = [json.loads(line) for line in ledger.read_text().splitlines()][-1]
    assert closeout["match"] == "prompt_hash_ambiguous"
    assert closeout["matched"] is False
    assert closeout["task_class"] is None
    assert closeout["subagent_type"] is None


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_replay_closes_the_seat_a_historical_exact_closeout_named(interpreter: str, tmp_path: Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    digest = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    rows = [
        {
            "event": "dispatch",
            "session_id": SESSION,
            "ts": "2026-09-22T10:00:00Z",
            "subagent_type": "opus-seat",
            "task_class": "implement",
            "model": "opus",
            "prompt_sha256": digest,
        },
        {
            "event": "dispatch",
            "session_id": SESSION,
            "ts": "2026-09-22T10:00:01Z",
            "subagent_type": "cursor-seat",
            "task_class": "mechanical",
            "model": "grok",
            "prompt_sha256": digest,
        },
        # An older hook joined this stop exactly to the OLDER opus-seat dispatch.
        {
            "event": "closeout",
            "session_id": SESSION,
            "subagent_type": "opus-seat",
            "prompt_sha256": digest,
            "match": "prompt_hash",
            "matched": True,
        },
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    transcript = tmp_path / "subagents" / "agent-1.jsonl"
    transcript.parent.mkdir()
    _write_transcript(transcript)
    payload = {
        "session_id": SESSION,
        "agent_id": "w",
        "agent_type": "cursor-seat",
        "agent_transcript_path": str(transcript),
        "last_assistant_message": "Done.",
    }
    subprocess.run(
        [interpreter, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        check=True,
        env={**os.environ, "DISPATCH_LEDGER": str(ledger)},
    )
    closeout = [json.loads(line) for line in ledger.read_text().splitlines()][-1]
    # Replay closed opus-seat, so the only open sibling is cursor-seat: an exact join.
    assert closeout["match"] == "prompt_hash"
    assert closeout["subagent_type"] == "cursor-seat"
    assert closeout["task_class"] == "mechanical"
