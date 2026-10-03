from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

EVAL = Path(__file__).resolve().parents[2] / "scripts" / "claude_cache_eval.py"


def load_eval():
    loader = importlib.machinery.SourceFileLoader("claude_cache_eval_test", str(EVAL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def usage(write: int, read: int = 0, incoming: int = 0, five: int = 0, hour: int = 0) -> dict:
    return {
        "input_tokens": incoming,
        "cache_creation_input_tokens": write,
        "cache_read_input_tokens": read,
        "cache_creation": {
            "ephemeral_5m_input_tokens": five,
            "ephemeral_1h_input_tokens": hour,
        },
    }


def transcript_line(msg_id: str, when_ms: int, body: dict, *, model: str = "claude-sonnet") -> str:
    stamp = datetime.fromtimestamp(when_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps({
        "type": "assistant",
        "timestamp": stamp,
        "message": {"id": msg_id, "model": model, "usage": body},
    })


def test_assign_turns_by_mark_windows() -> None:
    ev = load_eval()
    marks = [
        {"turn": "T1", "startedAt": 1_000, "endedAt": 2_000},
        {"turn": "T2", "startedAt": 8_000, "endedAt": 9_000},
        {"turn": "T3", "startedAt": 15_000, "endedAt": 16_000},
    ]
    lines = [
        transcript_line("m1", 1_500, usage(10, five=4, hour=1)),
        transcript_line("m1", 1_600, usage(99)),
        transcript_line("gap", 5_000, usage(50)),
        transcript_line("syn", 1_700, usage(80), model="<synthetic>"),
        transcript_line("m3", 15_500, usage(30_000, five=20, hour=3)),
        "{not json",
    ]
    assigned = ev.assign_turns(lines, marks)
    assert [item["cache_creation_input_tokens"] for item in assigned["T1"]] == [10]
    assert assigned["T2"] == []
    assert assigned["T3"][0]["cache_creation_input_tokens"] == 30_000


def test_tier_split_sums() -> None:
    ev = load_eval()
    totals = ev.sum_tiers([
        usage(12, read=3, five=5, hour=7),
        usage(3, read=4, five=1, hour=2),
    ])
    assert totals == {
        "cache_read": 7,
        "cache_write": 15,
        "ephemeral_5m_input_tokens": 6,
        "ephemeral_1h_input_tokens": 9,
    }


def test_sdk_verdict_ignores_idle_and_resume_busts() -> None:
    ev = load_eval()
    quiet = usage(10, read=1_000)
    bust = usage(30_000)
    passing = ev.sdk_verdict({
        "T1": [usage(100)],
        "T2": [quiet],
        "T3": [quiet],
        "T4": [bust],
        "T5": [bust],
    })
    assert passing["pass"] is True
    assert passing["failures"] == []
    assert passing["phases"]["live"]["bust"] is False
    assert passing["phases"]["idle"]["bust"] is True
    assert passing["phases"]["resume"]["bust"] is True

    failing = ev.sdk_verdict({
        "T1": [usage(100)],
        "T2": [quiet],
        "T3": [bust],
        "T4": [quiet],
        "T5": [quiet],
    })
    assert failing["pass"] is False
    assert failing["phases"]["live"]["bust"] is True
    assert failing["phases"]["idle"]["bust"] is False
    assert failing["phases"]["resume"]["bust"] is False


def test_sdk_verdict_fails_when_t3_has_no_records() -> None:
    ev = load_eval()
    quiet = usage(10, read=1_000)
    verdict = ev.sdk_verdict({
        "T1": [usage(100)],
        "T2": [quiet],
        "T3": [],
        "T4": [quiet],
        "T5": [quiet],
    })
    assert verdict["pass"] is False
    assert verdict["failures"] == ["T3 had no records"]
    assert verdict["phases"]["live"]["bust"] is False


def test_sdk_arm_transcript_root_ignores_inherited_config_dir(monkeypatch) -> None:
    ev = load_eval()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/inherited-claude")
    sdk = ev.parse_args(["--arm", "sdk"])
    assert sdk.config_dir == ev.HOME / ".claude"
    assert ev.transcript_root(sdk) == ev.HOME / ".claude"
    explicit = ev.parse_args(["--arm", "sdk", "--config-dir", "/var/explicit-claude"])
    assert ev.transcript_root(explicit) == Path("/var/explicit-claude")
    cli = ev.parse_args([])
    assert ev.transcript_root(cli) == Path("/tmp/inherited-claude")


def test_cli_arg_defaults_match_today() -> None:
    ev = load_eval()
    args = ev.parse_args([])
    assert args.model == "sonnet"
    assert args.timeout == 600
    assert args.launcher == ev.LAUNCHER
    assert args.arm == "cli"
