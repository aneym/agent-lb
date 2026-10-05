from __future__ import annotations

import json
import runpy
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth.dependencies import validate_dashboard_session
from app.dependencies import get_accounts_context
from app.modules.pools.api import router
from app.modules.pools.plan import build_plan
from app.modules.pools.schemas import PoolSummary
from app.modules.pools.service import PoolsService, build_pools
from tests.unit.test_pools_api import _summary
from tests.unit.test_route_ladder import setup

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 30, 13, tzinfo=timezone.utc)


def inputs(*, head_maker="openai"):
    sol = {"id": "sol-medium", "seat": "gpt-implementer", "model": "sol-latest", "maker": "openai"}
    grok = {"id": "grok-low", "seat": "cursor-seat", "model": "grok-latest-low", "maker": "xai"}
    devin = {"id": "swe2-high", "seat": "devin-seat", "model": "swe-latest", "maker": "cognition"}
    table = {"ladder": "interim", "ladders": {"interim": {
        "implement": [sol, grok, devin] if head_maker == "openai" else [grok, sol, devin],
        "mechanical": [grok, devin, sol],
        "explore": [sol, devin],
        "verify": {
            "by_author_maker": {
                "openai": ["@sonnet-high"], "xai": ["@sonnet-high"], "anthropic": ["@sol-xhigh"],
            },
            "rungs": {
                "sonnet-high": {"seat": "sonnet-verifier", "model": "sonnet-latest", "maker": "anthropic"},
                "sol-xhigh": {"seat": "codex-verifier", "model": "sol-latest", "maker": "openai"},
            },
        },
    }}}
    costs = json.loads((REPO / "config/coding-agents/model-costs.json").read_text())
    return table, costs


def live_pools():
    response = build_pools([
        *[_summary(str(i), secondary_remaining=0 if i < 5 else 80) for i in range(8)],
        *[_summary(f"sol{i}", provider="openai", secondary_remaining=16.6) for i in range(5)],
    ], generated_at=NOW)
    response.pools.extend([
        PoolSummary(id="cursor-models", provider="cursor", kind="cli_seat_budget", accounts=1,
                    eligible_accounts=1, status="ok", percent_used=2,
                    cycle_reset_at=datetime(2026, 10, 20, tzinfo=timezone.utc)),
        PoolSummary(id="devin", provider="devin", kind="cli_seat", accounts=2, eligible_accounts=1, status="ok"),
    ])
    return response


@pytest.mark.asyncio
async def test_live_plan_endpoint_and_cli(tmp_path, monkeypatch, capsys):
    # The approved trial head is supplied as installed policy, not hard-coded by the plan.
    table, costs = inputs(head_maker="xai")
    path = tmp_path / "routing-table.json"
    path.write_text(json.dumps(table))
    path.with_name("model-costs.json").write_text(json.dumps(costs))
    monkeypatch.setenv("ROUTE_TABLE", str(path))

    async def pools(self):
        return live_pools()

    monkeypatch.setattr(PoolsService, "get_pools", pools)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[validate_dashboard_session] = lambda: None
    app.dependency_overrides[get_accounts_context] = lambda: SimpleNamespace(service=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/api/pools/plan")
    assert result.status_code == 200
    plan = result.json()
    assert plan["recommended"] == "balanced"
    deltas = {level: {row["pool"]: row["delta"] for row in body["accounts"]}
              for level, body in plan["levels"].items()}
    assert deltas == {
        "budget": {"anthropic": 0, "openai": -2, "cursor": 0, "devin": -2},
        "balanced": {"anthropic": 0, "openai": 0, "cursor": 0, "devin": 0},
        "unlimited": {"anthropic": 5, "openai": 1, "cursor": 1, "devin": 0},
    }
    assert plan["levels"]["budget"]["ladders"]["implement"][0]["model"] == "grok-4.7-low"
    head_row = next(item for item in costs if item["rung"] == "grok-low")
    risk = plan["levels"]["balanced"]["risk"]
    assert f"passed {head_row['accepted']} of {head_row['of']} units" in risk
    assert f"({head_row['source']})" in risk
    assert ("20-unit round" in risk) == (head_row["of"] < 20)
    assert all(r["harness"] != "Devin" for job in plan["levels"]["budget"]["ladders"].values() for r in job)
    assert [r["model"] for r in plan["levels"]["unlimited"]["ladders"]["review"]] == [
        "claude-opus-5-5", "gpt-6.1-sol"]
    assert plan["costs"][0]["minutesPerUnit"] == costs[0]["minutesPerUnit"]
    env = setup(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    (Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools_plan.json").write_text(json.dumps(plan))
    cli = runpy.run_path(str(REPO / "clients/route"))
    assert cli["main"](["plan"]) == 0
    text = capsys.readouterr().out
    assert "Balanced (recommended)" in text and "Flag a second plan" in text
    assert cli["main"](["plan", "--level", "budget"]) == 0
    assert "Drop 2" in capsys.readouterr().out
    assert cli["main"](["plan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == plan


def test_balanced_risk_settles_at_twenty_units():
    table, costs = inputs(head_maker="xai")
    costs = deepcopy(costs)
    row = next(item for item in costs if item["rung"] == "grok-low")
    row["of"] = 20
    risk = build_plan(live_pools(), table, costs)["levels"]["balanced"]["risk"]
    assert "20 units" in risk
    assert "20-unit round" not in risk


def test_balanced_risk_offers_a_twenty_unit_round_under_twenty():
    table, costs = inputs(head_maker="xai")
    costs = deepcopy(costs)
    next(item for item in costs if item["rung"] == "grok-low")["of"] = 6
    risk = build_plan(live_pools(), table, costs)["levels"]["balanced"]["risk"]
    assert "20-unit round" in risk


def test_policy_and_capacity_change_recommendations():
    table, costs = inputs()
    pools = live_pools()
    plan = build_plan(pools, table, costs)
    assert next(a for a in plan["levels"]["budget"]["accounts"] if a["pool"] == "openai")["delta"] == 0
    assert next(a for a in plan["levels"]["unlimited"]["accounts"] if a["pool"] == "cursor")["delta"] == 0
    table, costs = inputs(head_maker="xai")
    plan = build_plan(pools, table, costs)
    assert next(a for a in plan["levels"]["budget"]["accounts"] if a["pool"] == "openai")["delta"] == -2
    assert next(a for a in plan["levels"]["unlimited"]["accounts"] if a["pool"] == "cursor")["delta"] == 1
    cursor = next(pool for pool in pools.pools if pool.id == "cursor-models")
    cursor.percent_used = 61
    pools.generated_at = datetime(2026, 9, 25, tzinfo=timezone.utc)
    plan = build_plan(pools, table, costs)
    assert next(a for a in plan["levels"]["balanced"]["accounts"] if a["pool"] == "cursor")["delta"] == 1
    pools.generated_at = datetime(2026, 10, 10, tzinfo=timezone.utc)
    costs = deepcopy(costs)
    costs[0]["minutesPerUnit"] = 20
    plan = build_plan(pools, table, costs)
    assert next(a for a in plan["levels"]["balanced"]["accounts"] if a["pool"] == "cursor")["delta"] == 0
    assert next(a for a in plan["levels"]["budget"]["accounts"] if a["pool"] == "devin")["delta"] == 0
    # A five-hour empty window is not a weekly-empty Claude account.
    pools = build_pools([_summary("short", primary_remaining=0, secondary_remaining=80)], generated_at=NOW)
    assert build_plan(pools, table, costs)["levels"]["unlimited"]["accounts"][0]["delta"] == 0


def test_weekly_api_cohorts_and_menu_units(tmp_path, monkeypatch, capsys):
    resets = [NOW + timedelta(days=3, minutes=m) for m in (0, 9, 32)]
    response = build_pools([
        *[_summary(str(i), provider="openai", secondary_remaining=0, reset_at_secondary=reset)
          for i, reset in enumerate(resets)],
        _summary("canceled", provider="openai", secondary_remaining=0, reset_at_secondary=resets[0],
                 subscription_status="canceled"),
    ], generated_at=NOW)
    pool = next(p for p in response.model_dump(mode="json", by_alias=True)["pools"] if p["id"] == "openai-codex")
    assert pool["refills"] == [{"at": "2026-10-03T13:00:00Z", "accounts": 3, "remainingPercent": 0.0}]
    assert pool["totalAccounts"] == 4 and pool["usableAccounts"] == 0
    env = setup(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fixture = Path(env["ROUTE_FIXTURE_DIR"])
    (fixture / "api_pools.json").write_text(response.model_dump_json(by_alias=True))
    cli = runpy.run_path(str(REPO / "clients/route"))
    assert cli["main"](["menu"]) in (0, cli["EXIT_UNROUTABLE"])
    assert "openai-codex refills: 10-03 13:00Z +3 accounts" in capsys.readouterr().out


def test_active_policy_fallback_overrides_and_canonical_table():
    table, costs = inputs(head_maker="xai")
    table["ladders"]["baseline"] = {}
    table["classes"] = {"implement": {"chain": [
        {"seat": "cursor-seat", "model": "grok-latest-low", "maker": "xai"},
        {"seat": "gpt-implementer", "model": "sol-latest", "maker": "openai"},
    ]}}
    table["implement_default"] = "gpt-implementer"
    table["ladder"] = "baseline"
    plan = build_plan(live_pools(), table, costs)
    assert plan["levels"]["budget"]["accounts"][1]["delta"] == 0
    assert plan["levels"]["unlimited"]["accounts"][2]["delta"] == 0
    table["ladder"] = "interim"
    table["overrides"] = [{"class": "implement", "demote": {"seat": "cursor-seat"}}]
    assert build_plan(live_pools(), table, costs)["levels"]["budget"]["accounts"][1]["delta"] == 0
    table["overrides"] = []
    table["ladders"]["interim"]["implement"][0]["gate"] = {}
    assert build_plan(live_pools(), table, costs)["levels"]["budget"]["accounts"][1]["delta"] == 0
    table.pop("ladders")
    table.pop("classes")
    plan = build_plan(live_pools(), table, costs)
    assert all(a["change"] == "keep" and a["reason"] == "head unknown"
               for level in plan["levels"].values() for a in level["accounts"])
    canonical = json.loads((REPO / "config/coding-agents/routing-table.json").read_text())
    cli = runpy.run_path(str(REPO / "clients/route"))
    expected = next(entry["id"] for entry in cli["ladder_entries"](canonical, "implement")
                    if entry.get("gate", {"open": True}).get("open") is True)
    assert build_plan(live_pools(), canonical, costs)["levels"]["balanced"]["ladders"]["implement"][0]["id"] == expected
    pools = live_pools()
    pools.pools[2].accounts = 3  # Only one drop is safe when two accounts must remain.
    table, costs = inputs(head_maker="xai")
    plan = build_plan(pools, table, costs)
    assert plan["levels"]["budget"]["accounts"][1]["delta"] == -1
    assert "Keep 2" in plan["levels"]["budget"]["accounts"][1]["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["missing-table", "invalid-table", "invalid-costs", "missing-costs"])
async def test_plan_input_errors_use_dashboard_envelope(tmp_path, monkeypatch, broken):
    from app.core.handlers import add_exception_handlers
    import app.modules.pools.plan as plan_module

    table, costs = inputs()
    path = tmp_path / "routing-table.json"
    path.write_text(json.dumps(table))
    path.with_name("model-costs.json").write_text(json.dumps(costs))
    monkeypatch.setenv("ROUTE_TABLE", str(path))
    monkeypatch.setattr(plan_module, "_CONFIG", tmp_path)
    if broken == "missing-table":
        path.unlink()
    elif broken == "invalid-table":
        path.write_text("[]")
    elif broken == "invalid-costs":
        path.with_name("model-costs.json").write_text("{}")
    else:
        path.with_name("model-costs.json").unlink()

    async def pools(self):
        return live_pools()

    monkeypatch.setattr(PoolsService, "get_pools", pools)
    app = FastAPI()
    add_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[validate_dashboard_session] = lambda: None
    app.dependency_overrides[get_accounts_context] = lambda: SimpleNamespace(service=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/pools/plan")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "account_plan_unavailable"


@pytest.mark.asyncio
async def test_cursor_shared_plan_count_and_weekly_only_cli_output(tmp_path, monkeypatch, capsys):
    from app.modules.pools.cli_seats import SeatAccount, SeatAccountsResponse
    import app.modules.pools.service as service_module

    table, costs = inputs(head_maker="xai")
    table["cli_pools"] = {"cursor": {"accounts": {
        "cursor-main": {"plan": "shared"}, "cursor-gmail": {"plan": "shared"},
    }}}
    table_path = tmp_path / "routing-table.json"
    table_path.write_text(json.dumps(table))
    monkeypatch.setenv("ROUTE_TABLE", str(table_path))
    seats = SeatAccountsResponse(source="seat_state", accounts=[
        SeatAccount(id="cursor-main", vendor="cursor", ready=True),
        SeatAccount(id="cursor-gmail", vendor="cursor", ready=True),
        SeatAccount(id="devin-main", vendor="devin", ready=True),
    ])
    monkeypatch.setattr(service_module, "read_seat_accounts", lambda: seats)

    class Accounts:
        async def list_accounts(self):
            return [_summary("a", secondary_remaining=80, reset_at_secondary=NOW + timedelta(days=7))]

    response = await PoolsService(Accounts()).get_pools()
    plan = build_plan(response, table, costs)
    assert plan["levels"]["balanced"]["accounts"][2]["count"] == 1
    wire = response.model_dump(mode="json", by_alias=True)
    assert all("refills" not in pool for pool in wire["pools"] if pool["windowLabel"] != "week"
               or pool["id"] == "anthropic-fable")
    env = setup(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fixture = Path(env["ROUTE_FIXTURE_DIR"])
    (fixture / "api_pools.json").write_text(json.dumps(wire))
    # Conflicting account cohorts must not replace the server's authoritative refills.
    (fixture / "api_accounts.json").write_text(json.dumps({"accounts": [{
        "provider": "anthropic", "status": "active", "resetAtSecondary": "2099-01-01T00:00:00Z",
        "usage": {"secondaryRemainingPercent": 0},
    }]}))
    cli = runpy.run_path(str(REPO / "clients/route"))
    assert cli["main"](["pools", "--json"]) == 0
    returned = json.loads(capsys.readouterr().out)
    assert next(p for p in returned["pools"] if p["id"] == "anthropic-general")["refills"] == next(
        p for p in wire["pools"] if p["id"] == "anthropic-general")["refills"]
    assert cli["main"](["pools"]) == 0
    lines = capsys.readouterr().out.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(("anthropic-fable ", "cursor ", "devin ")):
            assert index + 1 == len(lines) or not lines[index + 1].startswith("  refills:")


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor_config", [None, "invalid", {"accounts": None}, {"accounts": []},
                                          {"accounts": {"cursor-main": "invalid"}}])
async def test_malformed_cursor_grouping_preserves_pools(tmp_path, monkeypatch, cursor_config):
    from app.modules.pools.cli_seats import SeatAccount, SeatAccountsResponse
    import app.modules.pools.service as service_module

    table, costs = inputs()
    table["cli_pools"] = {"cursor": cursor_config}
    path = tmp_path / "routing-table.json"
    path.write_text(json.dumps(table))
    path.with_name("model-costs.json").write_text(json.dumps(costs))
    monkeypatch.setenv("ROUTE_TABLE", str(path))
    seats = SeatAccountsResponse(source="seat_state", accounts=[
        SeatAccount(id="cursor-main", vendor="cursor", ready=True),
        SeatAccount(id="cursor-gmail", vendor="cursor", ready=True),
    ])
    monkeypatch.setattr(service_module, "read_seat_accounts", lambda: seats)

    class Accounts:
        async def list_accounts(self):
            return []

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[validate_dashboard_session] = lambda: None
    app.dependency_overrides[get_accounts_context] = lambda: SimpleNamespace(service=Accounts())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/pools")
        plan = await client.get("/api/pools/plan")
    assert response.status_code == plan.status_code == 200
    pools = {pool["id"]: pool for pool in response.json()["pools"]}
    assert pools["anthropic-general"]["refills"] == []
    assert pools["openai-codex"]["refills"] == []
    assert pools["cursor"]["accounts"] == 2
    assert plan.json()["levels"]["balanced"]["accounts"][2]["count"] == 2


def test_server_refills_keep_account_based_next_refill_pace(tmp_path, monkeypatch, capsys):
    env = setup(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fixture = Path(env["ROUTE_FIXTURE_DIR"])
    server = [{"at": "2026-10-03T16:58:00Z", "accounts": 3, "remainingPercent": 1.0}]
    document = json.loads((fixture / "api_pools.json").read_text())
    document["pools"][0]["refills"] = server
    (fixture / "api_pools.json").write_text(json.dumps(document))
    (fixture / "api_accounts.json").write_text(json.dumps({"accounts": [{
        "provider": "openai", "status": "quota_exceeded", "usage": {"secondaryRemainingPercent": 1},
        "resetAtSecondary": "2026-10-03T16:58:00Z",
    }]}))
    cli = runpy.run_path(str(REPO / "clients/route"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 30, 13, tzinfo=timezone.utc)

    monkeypatch.setitem(cli["command_pools"].__globals__, "datetime", Clock)
    assert cli["main"](["pools", "--json"]) == 0
    pool = next(p for p in json.loads(capsys.readouterr().out)["pools"] if p["id"] == "openai-codex")
    assert pool["refills"] == server
    assert pool["pace"]["next_refill_h"] == pytest.approx(75 + 58 / 60)
