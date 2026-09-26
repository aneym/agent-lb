from __future__ import annotations

from datetime import datetime

import pytest

from app.core.balancer import AccountState, select_account
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy.load_balancer import RuntimeState, _state_from_account

pytestmark = pytest.mark.unit

_RAW_USAGES = (96.0, 93.0, 66.0, 35.0)


def _pressure_saturated_states() -> list[AccountState]:
    return [
        AccountState(
            f"acct-{int(raw)}",
            AccountStatus.ACTIVE,
            used_percent=100.0,
            secondary_used_percent=100.0,
            raw_used_percent=raw,
            raw_secondary_used_percent=raw,
            capacity_credits=50_400.0,
            secondary_reset_at=1_700_000_000 + index,
        )
        for index, raw in enumerate(_RAW_USAGES)
    ]


def test_reset_drain_keeps_accounts_exhausted_only_by_lease_pressure():
    result = select_account(_pressure_saturated_states(), now=1_700_000_000.0, routing_strategy="reset_drain")
    assert result.account is not None
    assert result.account.account_id in {f"acct-{int(raw)}" for raw in _RAW_USAGES}


def test_sequential_drain_keeps_accounts_exhausted_only_by_lease_pressure():
    result = select_account(_pressure_saturated_states(), routing_strategy="sequential_drain")
    assert result.account is not None
    assert result.account.account_id in {f"acct-{int(raw)}" for raw in _RAW_USAGES}


def test_drain_strategies_still_exclude_raw_exhausted_accounts():
    states = [
        AccountState(
            "raw-exhausted",
            AccountStatus.ACTIVE,
            used_percent=100.0,
            secondary_used_percent=100.0,
            raw_used_percent=100.0,
            raw_secondary_used_percent=100.0,
            capacity_credits=1_134.0,
            secondary_reset_at=1_700_000_300,
        ),
        AccountState(
            "pressure-only",
            AccountStatus.ACTIVE,
            used_percent=100.0,
            secondary_used_percent=100.0,
            raw_used_percent=66.0,
            raw_secondary_used_percent=66.0,
            capacity_credits=50_400.0,
            secondary_reset_at=1_700_086_400,
        ),
    ]
    for strategy in ("reset_drain", "sequential_drain"):
        result = select_account(states, now=1_700_000_000.0, routing_strategy=strategy)
        assert result.account is not None
        assert result.account.account_id == "pressure-only"


def test_state_from_account_stores_raw_usage_beside_pressure(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    account = Account(
        id="pro-1",
        chatgpt_account_id="chatgpt-pro-1",
        email="pro-1@example.com",
        plan_type="pro",
        access_token_encrypted=b"a",
        refresh_token_encrypted=b"r",
        id_token_encrypted=b"i",
        last_refresh=datetime(2025, 1, 1),
        status=AccountStatus.ACTIVE,
    )
    primary = UsageHistory(
        id=1,
        account_id=account.id,
        recorded_at=datetime(2025, 1, 1),
        window="primary",
        used_percent=35.0,
        reset_at=int(now + 3600),
        window_minutes=300,
    )
    secondary = UsageHistory(
        id=2,
        account_id=account.id,
        recorded_at=datetime(2025, 1, 1),
        window="secondary",
        used_percent=66.0,
        reset_at=int(now + 7 * 24 * 3600),
        window_minutes=10080,
    )

    state = _state_from_account(
        account=account,
        primary_entry=primary,
        secondary_entry=secondary,
        runtime=RuntimeState(leased_tokens=50_400.0),
    )

    assert state.raw_used_percent == 35.0
    assert state.raw_secondary_used_percent == 66.0
    assert state.used_percent == 100.0
    assert state.secondary_used_percent == 100.0
