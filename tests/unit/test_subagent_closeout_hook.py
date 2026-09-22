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
