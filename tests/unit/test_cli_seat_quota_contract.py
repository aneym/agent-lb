from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.modules.pools.cli_seats import (
    SeatAccount,
    SeatAccountsResponse,
    cli_seat_pools,
    cursor_budget_pools,
    read_seat_accounts,
)
from app.modules.usage.schemas import UsageSummaryResponse

EXAMPLE = Path(__file__).resolve().parents[2] / (
    "openspec/changes/cli-seat-usage-parity/contract/seat-usage.example.json"
)


def _keys_equal(actual, expected):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            if key in {"daily", "cooldowns"}:
                if expected[key]:
                    _keys_equal(next(iter(actual[key].values())), next(iter(expected[key].values())))
            else:
                _keys_equal(actual[key], expected[key])
    elif isinstance(expected, list) and expected:
        assert actual
        for item, reference in zip(actual, expected):
            _keys_equal(item, reference)


def test_seat_accounts_serialized_keys_match_canonical_contract(tmp_path):
    example = json.loads(EXAMPLE.read_text())["seatAccounts"]
    for account in example["accounts"]:
        account["today"]["okKnown"] = True
    records = {}
    for account in example["accounts"]:
        records[account["id"]] = {
            "vendor": account["vendor"],
            "auth": account["auth"],
            "enabled": account["enabled"],
            "identity": {"email": account["email"], "tier": account["tier"]},
            "quota": account["quota"],
            "cooldowns": account["cooldowns"],
            "runs": [
                {
                    "ts": run["ts"],
                    "model": run["model"],
                    "tokens_in": run["tokensIn"],
                    "tokens_out": run["tokensOut"],
                    "cache_read": run["cacheRead"],
                    "ok": run["ok"],
                    "wall_s": run["wallS"],
                }
                for run in account["recentRuns"]
            ],
            "daily": {
                day: {
                    model: {
                        "runs": usage["runs"],
                        "tokens_in": usage["tokensIn"],
                        "tokens_out": usage["tokensOut"],
                        "cache_read": usage["cacheRead"],
                        "tokens_known": True,
                    }
                    for model, usage in models.items()
                }
                for day, models in account["daily"].items()
            },
        }
    for account in example["accounts"]:
        if not records[account["id"]]["runs"] and account["today"]["models"]:
            records[account["id"]]["runs"] = [
                {
                    "ts": "2026-10-16T12:00:00Z",
                    "model": item["model"],
                    "ok": True,
                    "tokens_in": item["tokensIn"],
                    "tokens_out": item["tokensOut"],
                }
                for item in account["today"]["models"]
            ]
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "accounts": records,
                "updated_at": example["stateUpdatedAt"],
                "quota_refreshed_at": example["quotaRefreshedAt"],
            }
        )
    )
    result = read_seat_accounts(state, now=datetime(2026, 10, 16, 14, tzinfo=timezone.utc))
    payload = result.model_dump(mode="json", by_alias=True)
    for actual, expected in zip(payload["accounts"], example["accounts"]):
        assert actual.keys() == expected.keys()
        for key in ("quota", "today", "lastDay", "daily"):
            _keys_equal(actual[key], expected[key])
        if expected["recentRuns"]:
            _keys_equal(actual["recentRuns"], expected["recentRuns"])
    assert payload.keys() == example.keys()


def test_usage_activity_keys_match_canonical_contract():
    example = json.loads(EXAMPLE.read_text())["usageSummaryCursor"]
    example["seatActivity"]["okKnown"] = True
    result = UsageSummaryResponse.model_validate(example).model_dump(mode="json", by_alias=True)
    _keys_equal(result["seatActivity"], example["seatActivity"])
    assert result["provider"] == "cursor"


def test_pool_window_serialized_keys_match_example_and_shared_plan_counts_accounts():
    example = json.loads(EXAMPLE.read_text())
    reference = example["seatAccounts"]["accounts"][0]
    accounts = [
        SeatAccount(id=account_id, vendor="cursor", plan="shared", tier="Ultra", ready=True, quota=reference["quota"])
        for account_id in ("first", "second")
    ]
    pools = cursor_budget_pools(
        SeatAccountsResponse(source="seat_state", accounts=accounts),
        {"cursor": {"pools": [{"id": "cursor-models", "models": ["grok-*"], "budget_usd_by_tier": {"Ultra": 100}}]}},
        now=datetime(2026, 10, 16, 14, tzinfo=timezone.utc),
    )
    window = pools[0].model_dump(mode="json", by_alias=True)["windows"][0]
    _keys_equal(window, example["poolsExcerpt"][0]["windows"][0])
    assert window["measuredAccounts"] == 2
    assert window["usedPercent"] == 62
    muse = cli_seat_pools(
        SeatAccountsResponse(source="seat_state", accounts=[SeatAccount(id="muse", vendor="muse", ready=True)])
    )[0].model_dump(mode="json", by_alias=True)
    assert muse["windows"] == []
    assert muse["quotaSource"] == "unavailable"
