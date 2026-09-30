"""Stand-in readers (routing scope {#failover}, 2026-09-30): route shows which sessions run on a stand-in, so a picker
and a person both see that the factory is not on its intended models."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "clients" / "route"
TABLE = REPO / "config" / "coding-agents" / "routing-table.json"
STANDING = {"active": [{"session_id": "orch-1", "lane": "routing", "intent": "orchestrator", "intended": "claude-opus-5-5",
                        "running": "gpt-6.1-sol", "effort": "high", "reason": "rate_limit_error: rate limited on every account",
                        "since": "2026-09-30T04:10:00Z", "last_at": "2026-09-30T04:20:00Z",
                        "expected_return": "2026-09-30T06:49:00Z", "requests": 9}],
            "recent": []}


def env_for(tmp_path: Path, name: str, stand_ins: dict | None) -> dict[str, str]:
    fixtures = tmp_path / name
    fixtures.mkdir()
    (fixtures / "api_models.json").write_text(json.dumps({"models": [{"id": "gpt-6.1-sol"}, {"id": "claude-opus-5-5"}]}))
    (fixtures / "api_pools.json").write_text(json.dumps({"pools": [
        {"id": "openai-codex", "status": "ok", "accounts": 6, "eligibleAccounts": 5, "headroomPercent": 70.0},
        {"id": "anthropic-general", "status": "ok", "accounts": 8, "eligibleAccounts": 4, "headroomPercent": 70.0}]}))
    if stand_ins is not None:
        (fixtures / "api_pools_stand-ins.json").write_text(json.dumps(stand_ins))
    home = tmp_path / f"{name}-home"
    (home / ".claude").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "AGENT_LB_"))}
    env.update(HOME=str(home), AGENT_LB_URL="http://127.0.0.1:1", ROUTE_FIXTURE_DIR=str(fixtures),
               ROUTE_MODELS_CACHE=str(tmp_path / f"{name}-models.json"), ROUTE_TABLE=str(TABLE))
    return env


def route(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60, env=env,
                          check=False)


def test_route_says_who_is_standing_in_and_when_they_go_back(tmp_path: Path) -> None:
    env = env_for(tmp_path, "standing", STANDING)

    picked = json.loads(route(env, "pick", "plan", "--json").stdout)
    assert picked["standing_in"] == [{"session_id": "orch-1", "lane": "routing", "intended": "claude-opus-5-5",
                                      "running": "gpt-6.1-sol", "since": "2026-09-30T04:10:00Z",
                                      "expected_return": "2026-09-30T06:49:00Z"}]
    text = route(env, "pick", "plan")
    assert "standing in: routing on gpt-6.1-sol for claude-opus-5-5 since 2026-09-30T04:10:00Z, back ~2026-09-30T06:49:00Z" in text.stdout

    listed = route(env, "stand-ins", "--json")
    assert listed.returncode == 0 and json.loads(listed.stdout) == STANDING
    assert "routing" in route(env, "stand-ins").stdout

    # agent-lb without the endpoint (older build): nothing standing in, and pick still works.
    older = env_for(tmp_path, "older", None)
    assert json.loads(route(older, "pick", "plan", "--json").stdout)["standing_in"] == []
    assert route(older, "stand-ins", "--json").returncode == 0
