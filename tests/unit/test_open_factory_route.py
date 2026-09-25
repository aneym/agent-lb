"""`open-factory route`: the host keeps the move whatever the decider answers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "clients" / "open-factory" / "bin" / "open-factory"
TABLE = REPO / "tests" / "fixtures" / "route" / "routing-table.json"


PICK = '{"pick": %s, "abstain": %s, "confidence": 0.9, "fit": %s}'
# Jev leaves the chain head (Explore) for Cursor; %s is its fit for the head.
AWAY = '{"pick": "cursor-seat.cursor-grok-4.6-low-fast", "fit": 0.9, "fits": {"Explore.claude-sonnet-5": %s}}'


def fake_jev(home: Path, answer: str, exit_code: int = 0) -> None:
    path = home / ".local" / "bin" / "jev"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{answer}'\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "api_pools.json").write_text(
        json.dumps(
            {
                "pools": [
                    {"id": "anthropic-general", "status": "ok", "eligibleAccounts": 3, "weeklyPacePercent": 5.0},
                    {"id": "openai-codex", "status": "ok", "eligibleAccounts": 3},
                    {"id": "cursor", "status": "ok", "eligibleAccounts": 1},
                ]
            }
        ),
        encoding="utf-8",
    )
    return home


def route(home: Path, task_class: str) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("ROUTE_", "AGENT_LB_", "CLAUDE"))}
    env.update(
        HOME=str(home),
        AGENT_LB_URL="http://127.0.0.1:1",
        ROUTE_TABLE=str(TABLE),
        ROUTE_FIXTURE_DIR=str(home.parent / "fixtures"),
        ROUTE_LEDGER=str(home / "dispatch.jsonl"),
        PATH="/usr/bin:/bin",
    )
    proc = subprocess.run(
        [sys.executable, str(CLI), "route", "--class", task_class, "--json", "a task"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads(proc.stdout)
    ledger = [json.loads(line) for line in (home / "dispatch.jsonl").read_text().splitlines()]
    assert ledger == [receipt]
    return receipt


@pytest.mark.parametrize(
    ("answer", "exit_code", "seat", "validation", "fallback", "abstain"),
    [
        # A menu pick is re-checked and dispatched as picked.
        (PICK % ('"Explore.claude-sonnet-5"', "null", 0.9), 0, "Explore", "accepted", None, None),
        # A seat the host never offered is refused; the host's own chain decides.
        (PICK % ('"rogue.gpt-9"', "null", 0.9), 0, "Explore", "unknown_id", "route_pick", None),
        # An abstain falls back to the chain head.
        (PICK % ("null", '"low_fit"', 0.2), 0, "Explore", None, "route_pick", "low_fit"),
        # A pick away from the chain head is overruled while the head still fits the task...
        (AWAY % 0.9, 0, "Explore", "accepted", None, None),
        # ...and kept when the head does not fit.
        (AWAY % 0.3, 0, "cursor-seat", "accepted", None, None),
        # Jev down (exit 3) costs one fallback, not the dispatch.
        ("JEV UNAVAILABLE (quota)", 3, "Explore", None, "route_pick", "jev_unavailable"),
    ],
)
def test_route_keeps_the_move_with_the_host(
    home: Path,
    answer: str,
    exit_code: int,
    seat: str,
    validation: str | None,
    fallback: str | None,
    abstain: str | None,
) -> None:
    fake_jev(home, answer, exit_code)

    receipt = route(home, "explore")

    assert (receipt["seat"], receipt["validation"], receipt["fallback"], receipt["abstain"]) == (
        seat,
        validation,
        fallback,
        abstain,
    )
    assert {c["id"] for c in receipt["candidates"]} >= {"Explore.claude-sonnet-5"}
