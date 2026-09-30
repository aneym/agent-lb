"""Pacing both ways: a pool is running low when its last day of burn empties it before reset,
and running rich when it would reset with quota unused (routing scope, 2026-09-30)."""

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
TABLE = REPO / "tests" / "fixtures" / "route" / "routing-table.json"


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_pools_reports_running_low_and_running_rich_from_the_last_day_of_burn(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    pools = [
        # 40% a day ago, 10% now: 1.25 points an hour empties it in 8 h, 100 h before reset.
        {"id": "openai-codex", "status": "ok", "eligibleAccounts": 4, "aggregateRemainingPercent": 10.0,
         "weeklyRemainingPercent": 10.0, "weeklyResetAt": iso(now + timedelta(hours=100)), "weeklyPacePercent": 5.0},
        # 70% a day ago, 60% now, reset in 24 h: about 50 points left over at reset.
        {"id": "anthropic-general", "status": "ok", "eligibleAccounts": 3, "aggregateRemainingPercent": 60.0,
         "weeklyRemainingPercent": 60.0, "weeklyResetAt": iso(now + timedelta(hours=24)), "weeklyPacePercent": -20.0},
        # A month pool carries its own day of burn: 1 point in 24 h, 480 h left, 98.5 left.
        {"id": "cursor-other", "status": "ok", "eligibleAccounts": 1, "windowLabel": "month",
         "monthlyRemainingPercent": 98.5, "burn24hPercent": 1.0, "cycleResetAt": iso(now + timedelta(hours=480))},
        # No history and no burn: the weekly pace decides.
        {"id": "glm", "status": "ok", "eligibleAccounts": 1, "aggregateRemainingPercent": 50.0, "weeklyPacePercent": -30.0},
        {"id": "devin", "status": "exhausted", "eligibleAccounts": 0},
        {"id": "cursor-models", "status": "ok", "eligibleAccounts": 1, "windowLabel": "month"},
    ]
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "api_pools.json").write_text(json.dumps({"generatedAt": iso(now), "pools": pools}))
    history = tmp_path / "history.jsonl"
    day_ago = iso(now - timedelta(hours=24))
    history.write_text(
        json.dumps({"ts": day_ago, "pool": "openai-codex", "remaining": 40.0}) + "\n"
        + json.dumps({"ts": day_ago, "pool": "anthropic-general", "remaining": 70.0}) + "\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "AGENT_LB_"))}
    env.update(HOME=str(tmp_path), AGENT_LB_URL="http://127.0.0.1:1", ROUTE_TABLE=str(TABLE),
               ROUTE_FIXTURE_DIR=str(fixtures), ROUTE_POOL_HISTORY=str(history))

    result = subprocess.run([sys.executable, str(SCRIPT), "pools", "--json"], capture_output=True, text=True,
                            timeout=60, env=env, check=False)

    assert result.returncode == 0, result.stderr
    pace = {pool["id"]: pool["pace"] for pool in json.loads(result.stdout)["pools"]}
    assert (pace["openai-codex"]["state"], pace["openai-codex"]["basis"]) == ("low", "burn_24h")
    assert round(pace["openai-codex"]["empties_in_h"]) == 8
    # Its weekly pace (+5) alone would have called it on pace: the day of burn wins.
    assert (pace["anthropic-general"]["state"], pace["anthropic-general"]["basis"]) == ("rich", "burn_24h")
    assert round(pace["anthropic-general"]["leftover"]) == 50
    assert pace["cursor-other"]["state"] == "rich"
    assert pace["cursor-other"]["leftover"] == pytest.approx(78.5, abs=0.1)
    assert (pace["glm"]["state"], pace["glm"]["basis"]) == ("low", "pace")
    assert pace["devin"]["state"] == "exhausted"
    assert (pace["cursor-models"]["state"], pace["cursor-models"]["basis"]) == ("unknown", "none")
    # This read became a sample for the next one.
    sampled = {json.loads(line)["pool"] for line in history.read_text().splitlines() if json.loads(line)["ts"] != day_ago}
    assert {"openai-codex", "anthropic-general", "cursor-other", "glm"} <= sampled
