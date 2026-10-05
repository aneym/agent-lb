"""The interim ladder (routing scope, approved 2026-09-30 02:35Z), through the real `route` CLI and the canonical table."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "clients" / "route"
CANONICAL_TABLE = REPO / "config" / "coding-agents" / "routing-table.json"
CURSOR_LIST = ("printf 'grok-4.7-medium - Grok 4.7 Medium\\ngrok-4.7-medium-fast - Grok 4.7 Medium Fast\\n"
               "grok-4.7-low - Grok 4.7 Low\\ncomposer-2.5 - Composer 2.5\\nclaude-sonnet-5-5-high - Sonnet 5.5 High\\n'")
DEVIN_LIST = "printf '  swe-2-high  SWE-2 High\\n  swe-2-medium  SWE-2 Medium\\n'"


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def setup(tmp_path: Path, *, claude_eligible: int = 3, codex_low: bool = True) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    codex_left = 10.0 if codex_low else 60.0
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(exist_ok=True)
    (fixtures / "api_models.json").write_text(json.dumps({"models": [{"id": "gpt-6.1-sol"}]}))
    (fixtures / "api_pools.json").write_text(json.dumps({"generatedAt": iso(now), "pools": [
        {"id": "openai-codex", "status": "ok", "eligibleAccounts": 4, "aggregateRemainingPercent": codex_left,
         "weeklyRemainingPercent": codex_left, "weeklyResetAt": iso(now + timedelta(hours=100)), "weeklyPacePercent": 5.0},
        {"id": "anthropic-general", "status": "ok", "eligibleAccounts": claude_eligible, "aggregateRemainingPercent": 60.0,
         "weeklyRemainingPercent": 60.0, "weeklyResetAt": iso(now + timedelta(hours=84)), "weeklyPacePercent": 10.0,
         "headroomPercent": 80.0},
        {"id": "cursor-models", "status": "ok", "eligibleAccounts": 1, "windowLabel": "month"},
        {"id": "cursor-other", "status": "ok", "eligibleAccounts": 2, "windowLabel": "month",
         "monthlyRemainingPercent": 98.5, "burn24hPercent": 1.0, "cycleResetAt": iso(now + timedelta(hours=480))},
        {"id": "devin", "status": "ok", "eligibleAccounts": 1},
    ]}))
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"ts": iso(now - timedelta(hours=24)), "pool": "openai-codex", "remaining": 40.0}) + "\n"
                       if codex_low else "")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "AGENT_LB_"))}
    env.update(HOME=str(home), AGENT_LB_URL="http://127.0.0.1:1", ROUTE_FIXTURE_DIR=str(fixtures),
               ROUTE_POOL_HISTORY=str(history), ROUTE_MODELS_CACHE=str(tmp_path / "models.json"),
               ROUTE_CURSOR_MODELS_CMD=CURSOR_LIST, ROUTE_DEVIN_MODELS_CMD=DEVIN_LIST)
    return env


def table_copy(tmp_path: Path, name: str, edit) -> Path:
    table = json.loads(CANONICAL_TABLE.read_text(encoding="utf-8"))
    edit(table)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    return path


def reopen_cursor(table: dict) -> None:
    """Drop the out-of-usage gates on every Cursor rung, for tests of Cursor mechanics."""
    for rows in table["ladders"]["interim"].values():
        for row in rows if isinstance(rows, list) else ():
            if row.get("pool") == "cursor-models":
                row.pop("gate", None)


def cursor_open(tmp_path: Path) -> Path:
    return table_copy(tmp_path, "cursor-open", reopen_cursor)


def route(env: dict[str, str], table: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60,
                          env={**env, "ROUTE_TABLE": str(table)}, check=False)


def pick(env: dict[str, str], table: Path, *args: str) -> dict:
    result = route(env, table, "pick", *args, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_best_first_ladder_starts_with_approved_grok_medium(tmp_path: Path) -> None:
    env = setup(tmp_path)

    # While Grok and Composer are out of usage the canonical table gates both and Devin leads.
    gated = pick(env, CANONICAL_TABLE, "implement")
    assert gated["rung"] == "swe2-high"
    assert {row["rung"] for row in gated["skipped"] if row["reason"].startswith("gated:")} >= {"grok-medium", "composer"}

    grok_open = cursor_open(tmp_path)
    held = pick(env, grok_open, "implement")
    assert (held["ladder"], held["rung"], held["seat"], held["model"]) == (
        "interim", "grok-medium", "cursor-seat", "grok-4.7-medium")
    assert held["reason"] == "first open rung"
    assert held["pace"]["openai-codex"]["state"] == "low"
    assert (held["audit"]["rung"], held["audit"]["seat"], held["audit"]["model"], held["audit"]["effort"]) == (
        "sonnet-high", "sonnet-verifier", "claude-sonnet-5-5", "high")
    assert held["intended"] == "grok-medium"
    text = route(env, grok_open, "pick", "implement")
    assert text.returncode == 0, text.stderr
    assert text.stdout.strip().splitlines()[-1] == "→ grok-4.7-medium (cursor-models)"

    # Grok's work never goes to Grok or to Sonnet's own pool twice: Sonnet high, then Sol high.
    review = pick(env, CANONICAL_TABLE, "verify", "--author-vendor", "xai")
    assert (review["rung"], review["seat"], review["model"]) == ("sonnet-high", "sonnet-verifier", "claude-sonnet-5-5")
    retry = pick(env, CANONICAL_TABLE, "verify", "--author-vendor", "xai", "--skip", "sonnet-high")
    assert (retry["rung"], retry["seat"], retry["effort"]) == ("sol-high", "codex-verifier", "high")

    # Mechanical work starts with Composer when Cursor has usage, else Devin SWE medium.
    mechanical = pick(env, grok_open, "mechanical")
    assert (mechanical["rung"], mechanical["seat"], mechanical["model"]) == ("composer", "cursor-seat", "composer-2.5")
    held = pick(env, CANONICAL_TABLE, "mechanical")
    assert (held["rung"], held["seat"], held["model"]) == ("swe2-medium", "devin-seat", "swe-2-medium")

    # Claude on its last account, Codex healthy: orchestrators keep Claude, and Grok's review moves to Sol high.
    last = setup(tmp_path, claude_eligible=1, codex_low=False)
    reserved = pick(last, CANONICAL_TABLE, "verify", "--author-vendor", "xai")
    assert reserved["rung"] == "sonnet-cursor-high"
    assert (reserved["seat"], reserved["model"]) == ("cursor-seat", "claude-sonnet-5-5-high")
    assert {"rung": "sonnet-high", "reason": "reserved for orchestrators: 1 eligible account (keep 2)"} in reserved["skipped"]
    assert pick(last, CANONICAL_TABLE, "verify", "--author-vendor", "xai", "--skip", "sonnet-cursor-high")["rung"] == "sol-high"

    # The switch: baseline gives tonight's pre-ladder pick back on the next call.
    baseline = table_copy(tmp_path, "baseline", lambda t: t.update(ladder="baseline"))
    back = pick(env, baseline, "implement")
    assert (back["seat"], back["model"]) == ("gpt-implementer", "gpt-6.1-sol")
    assert back.get("rung") is None
