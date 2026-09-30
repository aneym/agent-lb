"""The orchestrator reserve (routing scope, 2026-09-30; Alex: "orchestrators must never go down"). With Claude on its
last usable account, or its best account under 40% headroom, only orchestrator-side classes start on Claude first;
review, explore and the rest go to another pool, and fall back to Claude only when nothing else is open."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "clients" / "route"
TABLE = REPO / "config" / "coding-agents" / "routing-table.json"


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def env_for(tmp_path: Path, name: str, *, eligible: int, headroom: float) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    fixtures = tmp_path / name
    fixtures.mkdir()
    (fixtures / "api_models.json").write_text(json.dumps({"models": [
        {"id": "gpt-6.1-sol"}, {"id": "claude-opus-5-5"}, {"id": "claude-sonnet-5-5"}]}))
    (fixtures / "api_pools.json").write_text(json.dumps({"generatedAt": iso(now), "pools": [
        {"id": "openai-codex", "status": "ok", "accounts": 6, "eligibleAccounts": 5, "headroomPercent": 70.0,
         "weeklyRemainingPercent": 60.0, "weeklyResetAt": iso(now + timedelta(hours=100))},
        {"id": "anthropic-general", "status": "ok", "accounts": 8, "eligibleAccounts": eligible,
         "headroomPercent": headroom, "weeklyRemainingPercent": 40.0, "weeklyResetAt": iso(now + timedelta(hours=84))},
    ]}))
    home = tmp_path / f"{name}-home"
    (home / ".claude").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "AGENT_LB_"))}
    env.update(HOME=str(home), AGENT_LB_URL="http://127.0.0.1:1", ROUTE_FIXTURE_DIR=str(fixtures),
               ROUTE_MODELS_CACHE=str(tmp_path / f"{name}-models.json"), ROUTE_TABLE=str(TABLE))
    return env


def pick(env: dict[str, str], *args: str) -> dict:
    result = subprocess.run([sys.executable, str(SCRIPT), "pick", *args, "--json"], capture_output=True, text=True,
                            timeout=60, env=env, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_last_claude_account_is_kept_for_orchestrators(tmp_path: Path) -> None:
    last = env_for(tmp_path, "last", eligible=1, headroom=50.0)

    # Tonight's live state: review used to start on Opus (plan-reviewer). Now it starts on Sol and says why.
    review = pick(last, "review")
    assert (review["seat"], review["pool"]) == ("codex-sol", "openai-codex")
    assert review["reserve"] == {"pool": "anthropic-general", "active": True, "admitted": False, "eligible": 1,
                                 "headroom": 50.0, "why": "1 eligible account (keep 2)"}
    assert review["fallbacks"][-1]["seat"] == "plan-reviewer"

    # Planning is orchestrator work: it keeps Claude first.
    plan = pick(last, "plan")
    assert plan["seat"] == "planner" and plan["reserve"]["admitted"] is True

    # Sol's work has no reviewer but Claude: the reserve bends rather than leave the work unreviewed, and says so.
    verify = pick(last, "verify", "--author-vendor", "openai")
    assert verify["seat"] == "verifier"
    assert verify["reason"].startswith("reserved for orchestrators: 1 eligible account (keep 2); no other seat open")

    # Enough accounts but the best is under 40%: the same rule.
    thin = pick(env_for(tmp_path, "thin", eligible=3, headroom=35.0), "review")
    assert thin["seat"] == "codex-sol" and thin["reserve"]["why"] == "headroom 35% (keep 40%)"

    # A healthy pool changes nothing.
    healthy = pick(env_for(tmp_path, "healthy", eligible=3, headroom=80.0), "review")
    assert healthy["seat"] == "plan-reviewer" and healthy["reserve"]["active"] is False


@pytest.mark.parametrize("field,value", [
    ("min_eligible", None),
    ("min_eligible", "2"),
    ("min_eligible", True),
    ("headroom_min_percent", None),
    ("headroom_min_percent", "40"),
    ("headroom_min_percent", False),
    ("admit_classes", None),
    ("admit_classes", "plan"),
    ("pool", None),
    ("pool", []),
])
def test_malformed_reserve_keeps_pick_and_menu_working(tmp_path: Path, field: str, value: object) -> None:
    env = env_for(tmp_path, "malformed", eligible=1, headroom=35.0)
    table = json.loads(TABLE.read_text())
    if value is None:
        del table["policy"]["reserve"][field]
    else:
        table["policy"]["reserve"][field] = value
    table_path = tmp_path / "routing-table.json"
    table_path.write_text(json.dumps(table))
    env["ROUTE_TABLE"] = str(table_path)

    review = pick(env, "review")
    assert review["seat"] == "plan-reviewer"
    assert review["reserve"] is None
    result = subprocess.run([sys.executable, str(SCRIPT), "menu", "--class", "review", "--json"], capture_output=True,
                            text=True, timeout=60, env=env, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["classes"]["review"]["seats"][0]["seat"] == "plan-reviewer"
