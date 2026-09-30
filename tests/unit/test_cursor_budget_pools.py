from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.modules.pools.cli_seats import (
    SeatAccount,
    SeatAccountsResponse,
    SeatModelUsage,
    SeatRun,
    cursor_budget_pools,
    read_seat_accounts,
)
from app.modules.pools.schemas import PoolSummary


def test_naive_utc_budget_time_is_independent_of_host_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    config = json.loads((Path(__file__).resolve().parents[2] / "config/coding-agents/routing-table.json").read_text())[
        "cli_pools"
    ]
    # Pin the test allowance; live dashboard calibration changes independently.
    config["cursor"]["pools"][1]["budget_usd_by_tier"]["Ultra"] = 400
    stamp = datetime(2026, 9, 30, 0, tzinfo=timezone.utc)
    seats = SeatAccountsResponse(
        source="seat_state",
        accounts=[
            SeatAccount(
                id="cursor-test",
                vendor="cursor",
                tier="Ultra",
                ready=True,
                daily={
                    "2026-09-30": {"claude-sonnet-5-5-high": SeatModelUsage(tokens_in=2_000_000, tokens_out=200_000)}
                },
                recent_runs=[
                    SeatRun(ts=stamp, model="claude-sonnet-5-5-high", tokens_in=2_000_000, tokens_out=200_000)
                ],
            )
        ],
    )
    original = os.environ.get("TZ")
    try:
        for zone in ("UTC", "Asia/Tokyo"):
            monkeypatch.setenv("TZ", zone)
            time.tzset()
            other = cursor_budget_pools(seats, config, now=datetime(2026, 9, 30, 1))[1]
            assert other.spent_usd == 6.0
            assert other.burn24h_percent == 1.5
    finally:
        if original is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original)
        time.tzset()


def test_budget_fields_are_in_serialization_schema() -> None:
    properties = PoolSummary.model_json_schema(mode="serialization")["properties"]
    assert {
        "spentUsd",
        "budgetUsd",
        "monthlyRemainingPercent",
        "monthlyPacePercent",
        "burn24hPercent",
        "cycleResetAt",
        "unbudgetedAccounts",
    } <= properties.keys()


def test_malformed_budget_table_entries_are_skipped() -> None:
    seats = SeatAccountsResponse(source="seat_state", accounts=[SeatAccount(id="c", vendor="cursor", ready=True)])
    config = {
        "cursor": {
            "pools": [None, {}, {"id": "valid", "models": ["*"], "budget_usd_by_tier": {}}],
            "prices_usd_per_mtok": [None, ["*", [5, 5, 25]]],
        }
    }
    assert [pool.id for pool in cursor_budget_pools(seats, config, now=datetime.now(timezone.utc))] == ["valid"]


@pytest.mark.asyncio
async def test_pools_api_keeps_accounts_with_corrupt_usage(
    async_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "accounts": {
                    "bad": {"vendor": "cursor", "daily": {"2026-09-30": {"grok-4.7-low": {"tokens_in": "abc"}}}},
                    "bad-run": {
                        "vendor": "cursor",
                        "auth_ok": True,
                        "runs": [{"ts": datetime.now(timezone.utc).isoformat(), "tokens_in": "abc"}],
                    },
                    "good": {"vendor": "cursor", "auth_ok": True},
                },
            }
        )
    )
    monkeypatch.setenv("AGENT_LB_SEAT_STATE", str(state))
    response = await async_client.get("/api/pools")
    assert response.status_code == 200
    cursor = next(pool for pool in response.json()["pools"] if pool["id"] == "cursor")
    assert cursor["accounts"] == 3
    assert cursor["eligibleAccounts"] == 3
    seats = read_seat_accounts(state)
    for seat in seats.accounts:
        assert seat.ready
        assert seat.daily == {}
        assert seat.recent_runs == []
        assert seat.last_day.tokens_in == 0
