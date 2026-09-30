"""Rollback, dispatch and refill contracts for the routing ladder follow-ups."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.test_route_ladder import pick, route, setup, table_copy
from tests.unit.test_seat_cursor_ids import cli as cursor_cli

cli = cursor_cli

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("malformed", ["reference", "object", "empty-explore"])
def test_seats_checks_inactive_ladder(tmp_path: Path, malformed: str) -> None:
    env = setup(tmp_path)

    def break_inactive(table):
        table["ladder"] = "baseline"
        if malformed == "object":
            table["ladders"]["interim"] = []
        elif malformed == "empty-explore":
            table["ladders"]["interim"]["explore"] = []
        else:
            table["ladders"]["interim"]["verify"]["by_author_maker"]["openai"] = ["@typo"]

    table = table_copy(tmp_path, "invalid", break_inactive)
    selected = pick(env, table, "implement")
    assert selected["seat"] == "gpt-implementer" and selected.get("rung") is None
    source = tmp_path / "policy-src"
    installed = tmp_path / "managed/coding-agents/routing-table.json"
    installed.parent.mkdir(parents=True)
    installed.write_text(table.read_text())
    env["AGENT_LB_POLICY_SRC"] = str(source)
    result = route(env, table, "seats", "--json")
    assert result.returncode == 3
    assert "route:" in result.stderr and "Traceback" not in result.stderr
    assert json.loads(result.stdout)["error"] == "invalid_ladder"


def test_cursor_closest_hint_excludes_retired_ids(cli) -> None:
    run, spawn = cli
    result = run("seat", "run", "--vendor", "cursor", "--model", "gpt-6.1-sol", "--", "fixture",
                 models="gpt-5.6-sol - Retired\ngpt-5.5-sol - Retired\ngpt-6-sol - Current")
    assert result.returncode != 0 and not spawn.exists()
    assert "closest: gpt-6-sol" in result.stderr
    assert "gpt-5.6" not in result.stderr and "gpt-5.5" not in result.stderr
