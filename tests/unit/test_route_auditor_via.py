"""Mechanical work keeps a reviewer when Claude is short but Cursor can run Sonnet."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.unit.test_route_reserve import SCRIPT, TABLE, env_for, pick


def audit_env(tmp_path: Path, *, claude: int = 1, cursor: int = 3) -> dict[str, str]:
    env = env_for(tmp_path, "audit", eligible=claude, headroom=50.0)
    table = json.loads(TABLE.read_text())
    table["ladder"] = "baseline"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(table))
    env["ROUTE_TABLE"] = str(baseline)
    env["ROUTE_CURSOR_MODELS_CMD"] = "printf 'claude-sonnet-5-5-high - Sonnet\\n'"
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    document["pools"].append({"id": "cursor-other", "status": "ok" if cursor else "exhausted",
                              "eligibleAccounts": cursor, "headroomPercent": 80.0})
    path.write_text(json.dumps(document))
    return env


@pytest.mark.parametrize("claude", [0, 1])
def test_mechanical_uses_cursor_auditor_when_claude_is_short(tmp_path: Path, claude: int) -> None:
    env = audit_env(tmp_path, claude=claude)
    result = pick(env, "mechanical")
    assert result["seat"] == "gpt-implementer"
    assert result["audit"]["via"] == "cursor"
    assert result["audit"]["seat"] == "cursor-seat"
    assert result["audit"]["pool"] == "cursor-other"
    assert result["audit"]["model"] == "claude-sonnet-5-5-high"
    assert "anthropic-general" in result["audit"]["reason"]
    assert "cursor-other" in result["audit"]["reason"]
    text = subprocess.run([sys.executable, str(SCRIPT), "pick", "mechanical"], capture_output=True,
                          text=True, timeout=60, env=env, check=False)
    assert text.returncode == 0, text.stderr
    assert any("cursor-seat/claude-sonnet-5-5-high via cursor" in line
               for line in text.stdout.splitlines() if line.startswith("audit"))


@pytest.mark.parametrize("down,expected", [("verifier", "gpt-implementer"), ("cursor-seat", "sonnet-implementer")])
def test_alternate_checks_cursor_seat_not_claude_seat(tmp_path: Path, down: str, expected: str) -> None:
    env = audit_env(tmp_path)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "seats": {down: {"ok": False}}}))
    env["ROUTE_STATE"] = str(state)
    result = pick(env, "mechanical")
    assert result["seat"] == expected
    if down == "verifier":
        assert result["audit"]["seat"] == "cursor-seat"
    else:
        assert "its auditor's pool anthropic-general is critical: 1 eligible" in result["reason"]


@pytest.mark.parametrize("unavailable", ["missing-pool", "missing-model", "model-list-unavailable"])
def test_alternate_requires_pool_and_listed_cursor_model(tmp_path: Path, unavailable: str) -> None:
    env = audit_env(tmp_path)
    if unavailable == "missing-pool":
        path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
        document = json.loads(path.read_text())
        document["pools"] = [pool for pool in document["pools"] if pool["id"] != "cursor-other"]
        path.write_text(json.dumps(document))
    elif unavailable == "missing-model":
        env["ROUTE_CURSOR_MODELS_CMD"] = "printf 'claude-sonnet-5-5-low - Sonnet\\n'"
    else:
        env["ROUTE_CURSOR_MODELS_CMD"] = "printf ''"
    result = pick(env, "mechanical")
    assert result["seat"] == "sonnet-implementer"
    assert "its auditor's pool anthropic-general is critical: 1 eligible" in result["reason"]
    assert "via" not in result["audit"]


@pytest.mark.parametrize("cursor", [0, 1])
def test_unavailable_cursor_keeps_primary_auditor_skip_reason(tmp_path: Path, cursor: int) -> None:
    result = pick(audit_env(tmp_path, cursor=cursor), "mechanical")
    assert result["seat"] == "sonnet-implementer"
    assert "its auditor's pool anthropic-general is critical: 1 eligible" in result["reason"]
    assert "via" not in result["audit"]


@pytest.mark.parametrize("alternate", [None, [], {"anthropic-general": []},
    {"anthropic-general": {"pool": [], "vendor": "cursor", "model": "claude-sonnet-5-5-high"}},
    {"anthropic-general": {"pool": "cursor-other", "model": "claude-sonnet-5-5-high"}},
    {"anthropic-general": {"pool": "cursor-other", "vendor": "cursor", "model": False}},
])
def test_malformed_auditor_via_preserves_existing_routing(tmp_path: Path, alternate: object) -> None:
    env = audit_env(tmp_path)
    table = json.loads(Path(env["ROUTE_TABLE"]).read_text())
    table["policy"]["auditor_via"] = alternate
    path = tmp_path / "routing-table.json"
    path.write_text(json.dumps(table))
    env["ROUTE_TABLE"] = str(path)
    result = pick(env, "mechanical")
    assert result["seat"] == "sonnet-implementer"
    assert "its auditor's pool anthropic-general is critical: 1 eligible" in result["reason"]
    assert "via" not in result["audit"]
