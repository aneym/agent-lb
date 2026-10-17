from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "clients" / "route"
CANONICAL_TABLE = REPO / "config" / "coding-agents" / "routing-table.json"
CURSOR_LIST = (
    "printf 'grok-4.7-medium - Grok 4.7 Medium\\ngrok-4.7-medium-fast - Grok 4.7 Medium Fast\\n"
    "grok-4.7-low - Grok 4.7 Low\\ncomposer-2.5 - Composer 2.5\\nclaude-sonnet-5-5-high - Sonnet 5.5 High\\n'"
)
DEVIN_LIST = "printf '  swe-2-high  SWE-2 High\\n  swe-2-medium  SWE-2 Medium\\n'"


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def setup(tmp_path: Path, *, claude_eligible: int = 3, codex_low: bool = True) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    codex_left = 10.0 if codex_low else 60.0
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(exist_ok=True)
    (fixtures / "api_models.json").write_text(json.dumps({"models": [{"id": "gpt-6.1-sol"}]}))
    (fixtures / "api_pools.json").write_text(
        json.dumps(
            {
                "generatedAt": iso(now),
                "pools": [
                    {
                        "id": "openai-codex",
                        "status": "ok",
                        "eligibleAccounts": 4,
                        "aggregateRemainingPercent": codex_left,
                        "weeklyRemainingPercent": codex_left,
                        "weeklyResetAt": iso(now + timedelta(hours=100)),
                        "weeklyPacePercent": 5.0,
                    },
                    {
                        "id": "anthropic-general",
                        "status": "ok",
                        "eligibleAccounts": claude_eligible,
                        "aggregateRemainingPercent": 60.0,
                        "weeklyRemainingPercent": 60.0,
                        "weeklyResetAt": iso(now + timedelta(hours=84)),
                        "weeklyPacePercent": 10.0,
                        "headroomPercent": 80.0,
                    },
                    {"id": "cursor-models", "status": "ok", "eligibleAccounts": 1, "windowLabel": "month"},
                    {
                        "id": "cursor-other",
                        "status": "ok",
                        "eligibleAccounts": 2,
                        "windowLabel": "month",
                        "monthlyRemainingPercent": 98.5,
                        "burn24hPercent": 1.0,
                        "cycleResetAt": iso(now + timedelta(hours=480)),
                    },
                    {"id": "devin", "status": "ok", "eligibleAccounts": 1},
                ],
            }
        )
    )
    history = tmp_path / "history.jsonl"
    history.write_text(
        json.dumps({"ts": iso(now - timedelta(hours=24)), "pool": "openai-codex", "remaining": 40.0}) + "\n"
        if codex_low
        else ""
    )
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "AGENT_LB_"))}
    env.update(
        HOME=str(home),
        AGENT_LB_URL="http://127.0.0.1:1",
        ROUTE_FIXTURE_DIR=str(fixtures),
        ROUTE_POOL_HISTORY=str(history),
        ROUTE_MODELS_CACHE=str(tmp_path / "models.json"),
        ROUTE_CURSOR_MODELS_CMD=CURSOR_LIST,
        ROUTE_DEVIN_MODELS_CMD=DEVIN_LIST,
    )
    return env


def table_copy(tmp_path: Path, name: str, edit) -> Path:
    table = json.loads(CANONICAL_TABLE.read_text(encoding="utf-8"))
    edit(table)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    return path
