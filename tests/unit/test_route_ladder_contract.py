"""Ladder output contracts at the CLI and its factory consumer boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.unit.test_route_ladder import CANONICAL_TABLE, pick, route, setup, table_copy

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "clients" / "open-factory"))
from open_factory.decide import candidates_for  # noqa: E402


@pytest.mark.parametrize("task", ["implement", "mechanical", "explore", "verify"])
def test_menu_candidates_start_with_the_pick(tmp_path: Path, task: str) -> None:
    env = setup(tmp_path)
    args = ("--author-vendor", "openai") if task == "verify" else ()
    selected = pick(env, CANONICAL_TABLE, task, *args)
    result = route(env, CANONICAL_TABLE, "menu", "--class", task, "--json")
    assert result.returncode == 0, result.stderr
    menu = json.loads(result.stdout)
    candidates = candidates_for(menu, task, {})
    assert candidates[0]["id"] == f"{selected['seat']}.{selected['model']}"
    assert all(row["vendor"] == row["maker"] for row in menu["classes"][task]["seats"])


def test_skipping_every_mechanical_rung_returns_json_error(tmp_path: Path) -> None:
    env = setup(tmp_path)
    table = json.loads(CANONICAL_TABLE.read_text())
    args = [value for rung in table["ladders"]["interim"]["mechanical"] for value in ("--skip", rung["id"])]
    result = route(env, CANONICAL_TABLE, "pick", "mechanical", *args, "--json")
    assert result.returncode == 2
    error = json.loads(result.stdout)
    assert error["error"] == "unroutable" and error["skipped"]


def test_ladder_pick_preserves_standing_in(tmp_path: Path) -> None:
    env = setup(tmp_path)
    fixture = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools_stand-ins.json"
    record = {"session_id": "job-1", "lane": "test", "intended": "sol", "running": "sonnet",
              "since": "2026-09-30T00:00:00Z", "expected_return": None}
    fixture.write_text(json.dumps({"active": [record], "recent": []}))
    result = pick(env, CANONICAL_TABLE, "implement")
    assert result["standing_in"] == [record]


@pytest.mark.parametrize("task,seat,rung", [
    ("implement", "cursor-seat", "grok-low"),
    ("explore", "cursor-seat", "composer"),
])
def test_unresolvable_sol_uses_cursor_worker(tmp_path: Path, task: str, seat: str, rung: str) -> None:
    env = setup(tmp_path)
    (Path(env["ROUTE_FIXTURE_DIR"]) / "api_models.json").write_text(json.dumps({"models": [{"id": "gpt-6-luna"}]}))
    selected = pick(env, CANONICAL_TABLE, task)
    assert (selected["seat"], selected["rung"]) == (seat, rung)


def test_seats_rejects_null_ladders(tmp_path: Path) -> None:
    env = setup(tmp_path)
    table = table_copy(tmp_path, "invalid", lambda value: value.update(ladders=None))
    clone = tmp_path / "policy-src"
    installed = clone.parent / "managed" / "coding-agents" / "routing-table.json"
    installed.parent.mkdir(parents=True)
    installed.write_text(table.read_text())
    env["AGENT_LB_POLICY_SRC"] = str(clone)
    result = route(env, table, "seats", "--json")
    assert result.returncode == 3 and "route:" in result.stderr
    assert json.loads(result.stdout)["error"] == "invalid_ladder"
