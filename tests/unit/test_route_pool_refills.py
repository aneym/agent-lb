"""Weekly refill inspection contracts."""
from __future__ import annotations

import json
import runpy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.unit.test_route_ladder import setup

REPO = Path(__file__).resolve().parents[2]


def refill_accounts() -> dict:
    return {"accounts": [
        {"id": str(index), "provider": "openai", "status": "active" if remaining else "quota_exceeded",
         "usage": {"secondaryRemainingPercent": remaining}, "resetAtSecondary": reset}
        for index, (remaining, reset) in enumerate([
            (76, "2026-10-07T13:00:00Z"), (0, "2026-10-03T16:58:00Z"),
            (1, "2026-10-03T17:07:00Z"), (0, "2026-10-03T17:30:00Z"),
            (0, "2026-10-03T21:06:00Z"),
        ])
    ]}


def test_weekly_refill_cohorts_and_next_low_account(tmp_path: Path, monkeypatch, capsys) -> None:
    env = setup(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fixtures = Path(env["ROUTE_FIXTURE_DIR"])
    accounts = refill_accounts()
    accounts["accounts"].append({
        "provider": "openai", "status": "quota_exceeded", "subscription": {"status": "canceled"},
        "usage": {"secondaryRemainingPercent": 0}, "resetAtSecondary": "2026-09-30T16:00:00Z"})
    (fixtures / "api_accounts.json").write_text(json.dumps(accounts))
    router = runpy.run_path(str(REPO / "clients/route"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 30, 13, tzinfo=timezone.utc)

    router["command_pools"].__globals__["datetime"] = Clock
    args = router["parse_args"](["pools", "--json"])
    assert router["command_pools"](args) == 0
    pools = {row["id"]: row for row in json.loads(capsys.readouterr().out)["pools"]}
    codex = pools["openai-codex"]
    assert codex["refills"] == [
        {"at": "2026-10-03T16:58:00Z", "accounts": 3, "remaining_percent": 1.0},
        {"at": "2026-10-03T21:06:00Z", "accounts": 1, "remaining_percent": 0.0},
        {"at": "2026-10-07T13:00:00Z", "accounts": 1, "remaining_percent": 76.0},
    ]
    assert codex["pace"]["next_refill_h"] == pytest.approx(75 + 58 / 60)
    assert "nextRefillAt" not in codex
    assert "refills" not in pools["anthropic-general"]
    assert "refills" not in pools["cursor-models"]
    args.json = False
    assert router["command_pools"](args) == 0
    assert "refills: 10-03 16:58Z +3, 10-03 21:06Z +1" in capsys.readouterr().out
    # An earlier healthy reset and a spent account's past reset do not set the next low refill.
    accounts = refill_accounts()
    accounts["accounts"].extend([
        {"provider": "openai", "status": "active", "usage": {"secondaryRemainingPercent": 80},
         "resetAtSecondary": "2026-10-01T13:00:00Z"},
        {"provider": "openai", "status": "quota_exceeded", "usage": {"secondaryRemainingPercent": 0},
         "resetAtSecondary": "2026-09-29T13:00:00Z"},
        {"provider": "openai", "status": "quota_exceeded", "usage": {"secondaryRemainingPercent": 0},
         "resetAtSecondary": "2026-10-01T12:00:00"},
    ])
    accounts["accounts"][1]["resetAtSecondary"] = "2026-10-03T12:58:00-04:00"
    (fixtures / "api_accounts.json").write_text(json.dumps(accounts))
    args.json = True
    assert router["command_pools"](args) == 0
    codex = next(row for row in json.loads(capsys.readouterr().out)["pools"] if row["id"] == "openai-codex")
    assert codex["pace"]["next_refill_h"] == pytest.approx(75 + 58 / 60)
    assert codex["refills"][1]["at"] == "2026-10-03T16:58:00Z"
    assert sum(row["accounts"] for row in codex["refills"]) == 6
    (fixtures / "api_accounts.json").unlink()
    args.json = True
    assert router["command_pools"](args) == 0
    assert all("refills" not in row for row in json.loads(capsys.readouterr().out)["pools"])


def test_refill_failure_does_not_break_pool_inspection(tmp_path: Path, monkeypatch, capsys) -> None:
    env = setup(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    (Path(env["ROUTE_FIXTURE_DIR"]) / "api_accounts.json").write_text(json.dumps(refill_accounts()))
    router = runpy.run_path(str(REPO / "clients/route"))

    def broken(document, body):
        document["pools"][0]["refills"] = []
        raise ValueError("unavailable weekly data")

    monkeypatch.setitem(router["fetch_pools"].__globals__, "add_pool_refills", broken)
    assert router["command_pools"](router["parse_args"](["pools", "--json"])) == 0
    assert all("refills" not in row for row in json.loads(capsys.readouterr().out)["pools"])


def test_refills_are_opt_in_and_never_include_fable(tmp_path: Path, monkeypatch) -> None:
    env = setup(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    router = runpy.run_path(str(REPO / "clients/route"))
    calls = []
    accounts = {"accounts": [{"provider": "anthropic", "status": "active", "fableEligible": True,
                              "usage": {"secondaryRemainingPercent": 1},
                              "resetAtSecondary": "2099-10-03T16:58:00Z"}]}

    def get(path, **kwargs):
        calls.append(path)
        body = accounts if path == "/api/accounts" else {"pools": [
            {"id": "anthropic-general"}, {"id": "anthropic-fable"}]}
        return router["HttpResult"](ok=True, status=200, body=body, error=None)

    monkeypatch.setitem(router["fetch_pools"].__globals__, "http_get", get)
    document, error = router["fetch_pools"]()
    assert error is None and calls == ["/api/pools"]
    assert all("refills" not in pool for pool in document["pools"])
    calls.clear()
    document, error = router["fetch_pools"](refills=True)
    assert error is None and calls == ["/api/pools", "/api/accounts"]
    assert "refills" in document["pools"][0]
    assert "refills" not in document["pools"][1]
