"""SubagentStop closeout records token cost from the subagent transcript."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

HOOK = Path(__file__).resolve().parents[2] / "config" / "coding-agents" / "hooks" / "subagent-closeout.py"
SYSTEM_PYTHON = "/usr/bin/python3"
INTERPRETERS = [sys.executable]
if Path(SYSTEM_PYTHON).is_file() and SYSTEM_PYTHON not in INTERPRETERS:
    INTERPRETERS.append(SYSTEM_PYTHON)

PROMPT = "compare the seats"
PARTIAL = {
    "input_tokens": 1,
    "cache_creation_input_tokens": 2,
    "cache_read_input_tokens": 4,
    "output_tokens": 8,
}
FINAL = {
    "input_tokens": 100,
    "cache_creation_input_tokens": 10,
    "cache_read_input_tokens": 40,
    "output_tokens": 25,
}
OTHER = {
    "input_tokens": 5,
    "cache_creation_input_tokens": 1,
    "cache_read_input_tokens": 7,
    "output_tokens": 3,
}


def _row(message: dict, timestamp: str | None = None) -> str:
    row: dict = {"message": message}
    if timestamp is not None:
        row["timestamp"] = timestamp
    return json.dumps(row)


def write_transcript(path: Path) -> None:
    lines = [
        _row({"role": "user", "content": PROMPT}, "2026-09-22T15:00:00.000Z"),
        _row(
            {"id": "msg_a", "role": "assistant", "model": "claude-haiku", "usage": PARTIAL},
            "2026-09-22T15:00:30.000Z",
        ),
        _row({"id": "msg_a", "role": "assistant", "model": "claude-sonnet", "usage": FINAL}),
        _row(
            {"id": "msg_b", "role": "assistant", "model": "seat-model", "usage": OTHER},
            "2026-09-22T15:01:30.500Z",
        ),
    ]
    path.write_text("\n".join(lines) + "\n")


def dispatch_line() -> dict:
    return {
        "event": "dispatch",
        "session_id": "sess-1",
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "subagent_type": "implementer",
        "name": "impl",
        "model": "dispatch-model",
        "ts": "2026-09-22T14:59:00Z",
    }


def invoke(python: str, payload: dict, ledger: Path) -> dict:
    result = subprocess.run(
        [python, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=os.environ | {"DISPATCH_LEDGER": str(ledger)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    closeouts = []
    for line in ledger.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") == "closeout":
            closeouts.append(record)
    assert len(closeouts) == 1
    return closeouts[0]


def payload(transcript: Path) -> dict:
    return {
        "session_id": "sess-1",
        "agent_id": "agent-1",
        "agent_type": "implementer",
        "last_assistant_message": "done",
        "agent_transcript_path": str(transcript),
    }


@pytest.mark.parametrize("python", INTERPRETERS)
def test_closeout_records_transcript_cost(tmp_path: Path, python: str) -> None:
    transcript = tmp_path / "agent.jsonl"
    write_transcript(transcript)
    ledger = tmp_path / "dispatch.jsonl"
    ledger.write_text(json.dumps(dispatch_line()) + "\n")
    closeout = invoke(python, payload(transcript), ledger)
    assert closeout["model"] == "seat-model"
    assert closeout["tokens_in"] == 163
    assert closeout["tokens_out"] == 28
    assert closeout["cache_read_tokens"] == 47
    assert closeout["wall_s"] == 90.5
    assert closeout["match"] == "prompt_hash"


@pytest.mark.parametrize("python", INTERPRETERS)
def test_missing_transcript_still_writes_null_cost(tmp_path: Path, python: str) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    ledger.write_text(json.dumps(dispatch_line()) + "\n")
    closeout = invoke(python, payload(tmp_path / "missing.jsonl"), ledger)
    assert closeout["model"] is None
    assert closeout["tokens_in"] is None
    assert closeout["tokens_out"] is None
    assert closeout["cache_read_tokens"] is None
    assert closeout["wall_s"] is None
