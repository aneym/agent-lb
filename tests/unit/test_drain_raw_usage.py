from __future__ import annotations

from datetime import datetime

import pytest

from app.core.balancer import AccountState, select_account
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy.load_balancer import RuntimeState, _state_from_account

pytestmark = pytest.mark.unit

_DRAIN_STRATEGIES = ["reset_drain", "sequential_drain"]


def _pressured_state(account_id: str, raw_used: float, raw_secondary: float) -> AccountState:
    # Effective usage is pinned at 100 by lease pressure; upstream usage is below it.
    return AccountState(
        account_id,
        AccountStatus.ACTIVE,
        used_percent=100.0,
        secondary_used_percent=100.0,
        raw_used_percent=raw_used,
        raw_secondary_used_percent=raw_secondary,
        capacity_credits=50_400.0,
        inflight_streams=4,
    )


@pytest.mark.parametrize("routing_strategy", _DRAIN_STRATEGIES)
def test_drain_selects_account_exhausted_only_by_pressure(routing_strategy):
    states = [
        _pressured_state("a", 10.0, 96.0),
        _pressured_state("b", 10.0, 93.0),
        _pressured_state("c", 10.0, 66.0),
        _pressured_state("d", 10.0, 35.0),
    ]

    result = select_account(states, routing_strategy=routing_strategy)

    assert result.account is not None
    assert result.error_message is None


@pytest.mark.parametrize("routing_strategy", _DRAIN_STRATEGIES)
def test_drain_excludes_account_with_raw_usage_exhausted(routing_strategy):
    states = [
        _pressured_state("a", 10.0, 100.0),
        _pressured_state("b", 100.0, 50.0),
        _pressured_state("c", 10.0, 40.0),
    ]

    result = select_account(states, routing_strategy=routing_strategy)

    assert result.account is not None
    assert result.account.account_id == "c"


@pytest.mark.parametrize("routing_strategy", _DRAIN_STRATEGIES)
def test_drain_reports_no_accounts_when_all_raw_usage_exhausted(routing_strategy):
    states = [
        _pressured_state("a", 10.0, 100.0),
        _pressured_state("b", 100.0, 20.0),
    ]

    result = select_account(states, routing_strategy=routing_strategy)

    assert result.account is None
    assert result.error_message == "No available accounts"


def test_drain_falls_back_to_effective_usage_without_raw_fields():
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=100.0, secondary_used_percent=100.0),
    ]

    result = select_account(states, routing_strategy="reset_drain")

    assert result.account is None


def test_state_from_account_keeps_raw_usage_next_to_pressure(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    account = Account(
        id="a",
        chatgpt_account_id="chatgpt-a",
        email="a@test.com",
        plan_type="pro",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"r",
        id_token_encrypted=b"i",
        last_refresh=datetime(2025, 1, 1),
        status=AccountStatus.ACTIVE,
    )
    recorded_at = datetime(2023, 11, 14, 22, 13)
    primary = UsageHistory(
        id=1,
        account_id="a",
        recorded_at=recorded_at,
        window="primary",
        used_percent=20.0,
        reset_at=int(now + 3600),
        window_minutes=300,
    )
    secondary = UsageHistory(
        id=2,
        account_id="a",
        recorded_at=recorded_at,
        window="secondary",
        used_percent=90.0,
        reset_at=int(now + 3 * 24 * 3600),
        window_minutes=10080,
    )

    state = _state_from_account(
        account=account,
        primary_entry=primary,
        secondary_entry=secondary,
        runtime=RuntimeState(inflight_streams=4),
    )

    assert state.raw_used_percent == 20.0
    assert state.raw_secondary_used_percent == 90.0
    assert state.used_percent == 30.0
    assert state.secondary_used_percent == 100.0
