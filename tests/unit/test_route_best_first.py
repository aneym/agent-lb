"""Best-first picks through the installed policy table and stubbed pool endpoint."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_route_ladder import CANONICAL_TABLE, SCRIPT, setup


def installed_policy(tmp_path: Path) -> tuple[dict[str, str], Path]:
    env = setup(tmp_path)
    table = Path(env["HOME"]) / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
    table.parent.mkdir(parents=True)
    table.write_text(CANONICAL_TABLE.read_text(), encoding="utf-8")
    return env, table


def pool_update(env: dict[str, str], pool_id: str, **fields) -> None:
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    next(pool for pool in document["pools"] if pool["id"] == pool_id).update(fields)
    path.write_text(json.dumps(document), encoding="utf-8")


def installed_pick(env: dict[str, str], task: str, *args: str) -> dict:
    result = subprocess.run([sys.executable, str(SCRIPT), "pick", task, *args, "--json"],
                            env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("task,rung", [
    ("implement", "grok-low"), ("mechanical", "composer"), ("explore", "sol-low"),
])
def test_low_codex_stays_first(tmp_path: Path, task: str, rung: str) -> None:
    env, _ = installed_policy(tmp_path)
    selected = installed_pick(env, task)
    assert selected["pace"]["openai-codex"]["state"] == "low"
    assert selected["rung"] == rung
    assert selected["reason"] == "first open rung"
    if task == "implement":
        assert selected["model"] == "grok-4.7-low"


@pytest.mark.parametrize("task,rung", [
    ("implement", "sol-medium"), ("mechanical", "swe2-medium"), ("explore", "sol-low"),
])
def test_empty_cursor_falls_back(tmp_path: Path, task: str, rung: str) -> None:
    env, _ = installed_policy(tmp_path)
    pool_update(env, "cursor-models", status="exhausted", eligibleAccounts=0)
    assert installed_pick(env, task)["rung"] == rung


@pytest.mark.parametrize("task,rung", [
    ("implement", "swe2-high"), ("mechanical", "swe2-medium"), ("explore", "swe2-medium"),
])
def test_empty_codex_and_cursor_fall_back_to_swe(tmp_path: Path, task: str, rung: str) -> None:
    env, _ = installed_policy(tmp_path)
    for pool in ("openai-codex", "cursor-models"):
        pool_update(env, pool, status="exhausted", eligibleAccounts=0)
    assert installed_pick(env, task)["rung"] == rung


def test_claude_stays_pace_guarded(tmp_path: Path) -> None:
    env, table_path = installed_policy(tmp_path)
    table = json.loads(table_path.read_text())
    table["ladders"]["interim"]["verify"]["rungs"]["sonnet-cursor-high"].pop("promote")
    table["policy"]["reserve"]["admit_classes"].append("verify")
    table_path.write_text(json.dumps(table), encoding="utf-8")
    pool_update(env, "anthropic-general", weeklyPacePercent=-20.0, headroomPercent=30.0)
    pool_update(env, "cursor-other", monthlyRemainingPercent=50.0, burn24hPercent=2.5)
    selected = installed_pick(env, "verify", "--author-vendor", "openai")
    assert selected["rung"] == "sonnet-cursor-high"
    assert any(row["rung"] == "sonnet-high" and row["reason"].startswith("running low")
               for row in selected["skipped"])


@pytest.mark.parametrize("author", ["xai", "cursor"])
def test_cursor_worker_makers_get_claude_reviewer(tmp_path: Path, author: str) -> None:
    env, _ = installed_policy(tmp_path)
    selected = installed_pick(env, "verify", "--author-maker", author)
    assert (selected["rung"], selected["seat"], selected["model"]) == (
        "sonnet-high", "sonnet-verifier", "claude-sonnet-5-5")


def test_missing_guarded_pools_keeps_old_pace_demotion(tmp_path: Path) -> None:
    env, table_path = installed_policy(tmp_path)
    table = json.loads(table_path.read_text())
    table["policy"]["pace"].pop("guarded_pools", None)
    table_path.write_text(json.dumps(table), encoding="utf-8")
    selected = installed_pick(env, "explore")
    assert selected["rung"] == "composer"
    assert any(row["rung"] == "sol-low" and row["reason"].startswith("running low")
               for row in selected["skipped"])
