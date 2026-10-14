

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.core.balancer import AccountState, RoutingCost, RoutingCostsByAccount, RoutingStrategy
from app.db.models import Account, AccountStatus, StickySessionKind
from app.modules.proxy import load_balancer as load_balancer_module
from app.modules.proxy.load_balancer import LoadBalancer

pytestmark = pytest.mark.unit


def _active(
    account_id: str,
    used_percent: float = 10.0,
    secondary_used_percent: float | None = None,
    routing_policy: str = "normal",
) -> AccountState:
    return AccountState(
        account_id,
        AccountStatus.ACTIVE,
        used_percent=used_percent,
        secondary_used_percent=secondary_used_percent,
        routing_policy=routing_policy,
    )


def _rate_limited(
    account_id: str,
    reset_at: float | None = None,
    cooldown_until: float | None = None,
    used_percent: float | None = None,
    routing_policy: str = "normal",
) -> AccountState:
    return AccountState(
        account_id,
        AccountStatus.RATE_LIMITED,
        used_percent=used_percent,
        reset_at=reset_at,
        cooldown_until=cooldown_until,
        error_count=1,
        last_error_at=time.time(),
        routing_policy=routing_policy,
    )


def _make_sticky_repo(existing_account_id: str | None = None) -> AsyncMock:
    repo = AsyncMock()
    repo.get_account_id = AsyncMock(return_value=existing_account_id)
    repo.upsert = AsyncMock()
    repo.delete = AsyncMock()
    return repo


async def _invoke_stickiness(
    states: list[AccountState],
    sticky_key: str,
    sticky_repo: AsyncMock,
    *,
    sticky_kind: StickySessionKind = StickySessionKind.PROMPT_CACHE,
    reallocate_sticky: bool = False,
    sticky_max_age_seconds: int | None = 600,
    budget_threshold_pct: float = 95.0,
    secondary_budget_threshold_pct: float = 100.0,
    headroom_reallocate: bool = False,
    burn_first_sticky_drain: bool = False,
    routing_strategy: RoutingStrategy = "usage_weighted",
    relative_availability_power: float = 2.0,
    relative_availability_top_k: int = 5,
    routing_costs_by_account_id: RoutingCostsByAccount | None = None,
):


    @asynccontextmanager
    async def mock_repo_factory():
        yield AsyncMock()

    lb = LoadBalancer(mock_repo_factory)
    account_map = {s.account_id: cast(Account, AsyncMock()) for s in states}

    return await lb._select_with_stickiness(
        states=states,
        account_map=account_map,
        sticky_key=sticky_key,
        sticky_kind=sticky_kind,
        reallocate_sticky=reallocate_sticky,
        sticky_max_age_seconds=sticky_max_age_seconds,
        budget_threshold_pct=budget_threshold_pct,
        secondary_budget_threshold_pct=secondary_budget_threshold_pct,
        headroom_reallocate=headroom_reallocate,
        burn_first_sticky_drain=burn_first_sticky_drain,
        prefer_earlier_reset_accounts=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy=routing_strategy,
        relative_availability_power=relative_availability_power,
        relative_availability_top_k=relative_availability_top_k,
        sticky_repo=sticky_repo,
        routing_costs_by_account_id=routing_costs_by_account_id,
    )







@pytest.mark.asyncio
async def test_fallback_does_not_overwrite_sticky_when_pinned_rate_limited():

    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_not_called()
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_all_accounts_unavailable_does_not_overwrite_sticky():

    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is None
    repo.upsert.assert_not_called()
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_overwrites_sticky_when_reallocate_sticky_true():

    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        sticky_kind=StickySessionKind.STICKY_THREAD,
        reallocate_sticky=True,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once()
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.STICKY_THREAD)


@pytest.mark.asyncio
async def test_sticky_preserved_then_returns_to_original_on_recovery():

    now = time.time()


    acc_a_down = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    r1 = await _invoke_stickiness(
        [acc_a_down, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )
    assert r1.account is not None
    assert r1.account.account_id == "b"
    repo.upsert.assert_not_called()


    acc_a_up = _active("a", used_percent=5.0)
    acc_b2 = _active("b", used_percent=20.0)
    repo2 = _make_sticky_repo(existing_account_id="a")

    r2 = await _invoke_stickiness(
        [acc_a_up, acc_b2],
        "key1",
        repo2,
        reallocate_sticky=False,
    )
    assert r2.account is not None
    assert r2.account.account_id == "a"


@pytest.mark.asyncio
async def test_sticky_deleted_when_pinned_account_removed_from_pool():

    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)







@pytest.mark.asyncio
async def test_all_accounts_exhausted_keeps_pinned_no_thrashing():
    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=97.0)
    acc_c = _active("c", used_percent=98.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_pool_exhausted_but_better_candidate_exists_reallocates():
    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)
    acc_c = _active("c", used_percent=97.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_round_robin_pool_health_check_prefers_budget_safe_candidate():
    now = time.time()
    acc_a = AccountState("a", AccountStatus.ACTIVE, used_percent=96.0, last_selected_at=now - 10)
    acc_b = AccountState("b", AccountStatus.ACTIVE, used_percent=50.0, last_selected_at=now - 1)
    acc_c = AccountState("c", AccountStatus.ACTIVE, used_percent=97.0, last_selected_at=now - 100)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key-round-robin",
        repo,
        reallocate_sticky=False,
        routing_strategy="round_robin",
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key-round-robin", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key-round-robin", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_sticky_fallback_applies_planner_costs():
    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b", used_percent=10.0)
    acc_c = _active("c", used_percent=30.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key-routing-cost",
        repo,
        reallocate_sticky=False,
        routing_costs_by_account_id={"b": RoutingCost(total=40.0), "c": RoutingCost(total=-5.0)},
    )

    assert result.account is not None
    assert result.account.account_id == "c"
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_capacity_weighted_pool_health_check_prefers_budget_safe_candidate():
    acc_a = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=96.0,
        secondary_used_percent=96.0,
        plan_type="pro",
        capacity_credits=50400.0,
    )
    acc_b = AccountState(
        "b",
        AccountStatus.ACTIVE,
        used_percent=50.0,
        secondary_used_percent=50.0,
        plan_type="free",
        capacity_credits=1134.0,
    )
    acc_c = AccountState(
        "c",
        AccountStatus.ACTIVE,
        used_percent=97.0,
        secondary_used_percent=97.0,
        plan_type="plus",
        capacity_credits=7560.0,
    )
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b, acc_c],
        "key-capacity-weighted",
        repo,
        reallocate_sticky=False,
        routing_strategy="capacity_weighted",
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key-capacity-weighted", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key-capacity-weighted", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_pool_exhausted_single_account_keeps_pinned():
    acc_a = _active("a", used_percent=96.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_pool_exhausted_with_custom_threshold():
    acc_a = _active("a", used_percent=85.0)
    acc_b = _active("b", used_percent=88.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
        budget_threshold_pct=80.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_pool_exhausted_candidate_with_none_usage_triggers_reallocation():
    acc_a = _active("a", used_percent=96.0)
    acc_b = AccountState("b", AccountStatus.ACTIVE, used_percent=None)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_first_request_creates_sticky_mapping():

    acc_a = _active("a", used_percent=10.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id=None)

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_first_sticky_request_honors_relative_availability_tuning():

    now = time.time()
    acc_a = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 10 * 60),
        capacity_credits=1_000.0,
    )
    acc_b = AccountState(
        "b",
        AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        secondary_reset_at=int(now + 11 * 60),
        capacity_credits=900.0,
    )
    repo = _make_sticky_repo(existing_account_id=None)

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key-relative-availability",
        repo,
        routing_strategy="relative_availability",
        relative_availability_power=10.0,
        relative_availability_top_k=1,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.upsert.assert_called_once_with("key-relative-availability", "a", kind=StickySessionKind.PROMPT_CACHE)







@pytest.mark.asyncio
async def test_grace_period_returns_pinned_when_reset_imminent():

    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 5)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_grace_period_keeps_rate_limited_pinned_account_even_when_usage_is_100_pct():
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 5, used_percent=100.0)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_grace_period_skipped_when_reset_far_away():

    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 300)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_grace_period_skipped_for_active_with_cooldown():

    now = time.time()

    acc_a = AccountState(
        "a",
        AccountStatus.ACTIVE,
        used_percent=10.0,
        cooldown_until=now + 2,
        error_count=1,
        last_error_at=now,
    )
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"


@pytest.mark.asyncio
async def test_grace_period_not_applied_for_reallocate_sticky():

    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 5)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        sticky_kind=StickySessionKind.STICKY_THREAD,
        reallocate_sticky=True,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once()


@pytest.mark.asyncio
async def test_grace_period_skipped_when_no_reset_at():

    now = time.time()
    acc_a = _rate_limited("a", reset_at=None, cooldown_until=now + 2)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_not_called()







@pytest.mark.asyncio
async def test_paused_pinned_account_persists_fallback():

    acc_a = AccountState("a", AccountStatus.PAUSED)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_reauth_required_pinned_account_persists_fallback():

    acc_a = AccountState("a", AccountStatus.REAUTH_REQUIRED, deactivation_reason="token expired")
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)







@pytest.mark.asyncio
async def test_grace_period_does_not_mutate_original_state():

    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 5)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    original_status = acc_a.status
    original_reset_at = acc_a.reset_at

    await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert acc_a.status == original_status
    assert acc_a.reset_at == original_reset_at


@pytest.mark.asyncio
async def test_codex_session_persists_fallback_during_outage():

    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.upsert.assert_called_once_with("session_123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_rate_limit_far_away_does_not_reallocate_codex_session_affinity():
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 1200)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("session_123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_rate_limit_far_away_without_fallback_preserves_codex_session_affinity():
    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 1200)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    )

    assert result.account is None
    repo.delete.assert_not_called()
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_budget_exhaustion_triggers_reallocation():

    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_budget_threshold_80_triggers_at_85_percent():
    acc_a = _active("a", used_percent=85.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
        budget_threshold_pct=80.0,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_budget_threshold_95_no_reallocation_at_85_percent():
    acc_a = _active("a", used_percent=85.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_budget_threshold_reallocates_codex_session_affinity():
    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "codex-session-123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("codex-session-123", kind=StickySessionKind.CODEX_SESSION)
    repo.upsert.assert_called_once_with("codex-session-123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_budget_threshold_reallocates_sticky_thread_affinity():

    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)

    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "thread-X",
        repo,
        sticky_kind=StickySessionKind.STICKY_THREAD,
        reallocate_sticky=True,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("thread-X", kind=StickySessionKind.STICKY_THREAD)
    repo.upsert.assert_called_once_with("thread-X", "b", kind=StickySessionKind.STICKY_THREAD)


@pytest.mark.asyncio
async def test_budget_threshold_reallocates_to_primary_safe_secondary_pressured_candidate():
    acc_a = _active("a", used_percent=99.0, secondary_used_percent=1.0)
    acc_b = _active("b", used_percent=10.0, secondary_used_percent=99.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "codex-session-123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("codex-session-123", kind=StickySessionKind.CODEX_SESSION)
    repo.upsert.assert_called_once_with("codex-session-123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_sticky_thread_preserves_pinned_when_pool_also_above_threshold():

    acc_a = _active("a", used_percent=99.0)
    acc_b = _active("b", used_percent=96.0)

    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "thread-Y",
        repo,
        sticky_kind=StickySessionKind.STICKY_THREAD,
        reallocate_sticky=True,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_budget_threshold_preserves_sticky_when_every_candidate_secondary_pressured():
    acc_a = _active("a", used_percent=10.0, secondary_used_percent=99.0)
    acc_b = _active("b", used_percent=20.0, secondary_used_percent=99.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "codex-session-123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_sticky_thread_below_threshold_does_not_reallocate():

    acc_a = _active("a", used_percent=85.0)
    acc_b = _active("b", used_percent=10.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "thread-Z",
        repo,
        sticky_kind=StickySessionKind.STICKY_THREAD,
        reallocate_sticky=True,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_sticky_mapping_uses_normal_budget_gate():
    acc_a = _active("a", used_percent=10.0, secondary_used_percent=99.0)
    acc_b = _active("b", used_percent=20.0, secondary_used_percent=10.0)
    repo = _make_sticky_repo(existing_account_id=None)

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "new-codex-session",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
        secondary_budget_threshold_pct=98.0,
        routing_strategy="round_robin",
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("new-codex-session", "a", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_secondary_budget_threshold_controls_sticky_reallocation():
    acc_a = _active("a", used_percent=10.0, secondary_used_percent=99.0)
    acc_b = _active("b", used_percent=20.0, secondary_used_percent=10.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "codex-session-123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
        budget_threshold_pct=95.0,
        secondary_budget_threshold_pct=98.0,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("codex-session-123", kind=StickySessionKind.CODEX_SESSION)
    repo.upsert.assert_called_once_with("codex-session-123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_rate_limit_far_away_triggers_reallocation():

    now = time.time()
    acc_a = _rate_limited("a", reset_at=now + 900)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [acc_a, acc_b],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("key1", kind=StickySessionKind.PROMPT_CACHE)
    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)


@pytest.mark.asyncio
async def test_burn_first_reallocation_only_when_burn_first_is_selectable():
    now = time.time()
    pinned = _active("a", used_percent=96.0)
    blocked_burn_first = _rate_limited(
        "b",
        reset_at=now + 1200,
        routing_policy="burn_first",
    )
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [pinned, blocked_burn_first],
        "key1",
        repo,
        reallocate_sticky=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()
    repo.upsert.assert_called_once_with("key1", "a", kind=StickySessionKind.PROMPT_CACHE)













@pytest.mark.asyncio
async def test_headroom_reallocation_rebinds_without_burn_first_target():

    pinned = _active("a", used_percent=96.0)
    safe = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [pinned, safe],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=None,
        headroom_reallocate=True,
    )

    assert result.account is not None
    assert result.account.account_id == "b"
    repo.delete.assert_called_once_with("session_123", kind=StickySessionKind.CODEX_SESSION)
    repo.upsert.assert_called_once_with("session_123", "b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_headroom_reallocation_rebinds_when_burn_first_unselectable():

    now = time.time()
    pinned = _active("a", used_percent=96.0)
    blocked_burn_first = _rate_limited("b", reset_at=now + 1200, routing_policy="burn_first")
    safe = _active("c", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [pinned, blocked_burn_first, safe],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=None,
        headroom_reallocate=True,
    )

    assert result.account is not None
    assert result.account.account_id == "c"
    repo.delete.assert_called_once_with("session_123", kind=StickySessionKind.CODEX_SESSION)
    repo.upsert.assert_called_once_with("session_123", "c", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_headroom_reallocation_disabled_keeps_pin_when_burn_first_unselectable():

    now = time.time()
    pinned = _active("a", used_percent=96.0)
    blocked_burn_first = _rate_limited("b", reset_at=now + 1200, routing_policy="burn_first")
    safe = _active("c", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [pinned, blocked_burn_first, safe],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=None,
        headroom_reallocate=False,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_headroom_reallocation_keeps_pin_when_pool_exhausted():

    pinned = _active("a", used_percent=96.0)
    also_exhausted = _active("b", used_percent=97.0)
    repo = _make_sticky_repo(existing_account_id="a")

    result = await _invoke_stickiness(
        [pinned, also_exhausted],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=None,
        headroom_reallocate=True,
    )

    assert result.account is not None
    assert result.account.account_id == "a"
    repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test_headroom_reallocation_no_flap_back_after_window_reset():


    repo = _make_sticky_repo(existing_account_id="a")
    r1 = await _invoke_stickiness(
        [_active("a", used_percent=96.0), _active("b", used_percent=50.0)],
        "session_123",
        repo,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=None,
        headroom_reallocate=True,
    )
    assert r1.account is not None and r1.account.account_id == "b"



    repo2 = _make_sticky_repo(existing_account_id="b")
    r2 = await _invoke_stickiness(
        [_active("a", used_percent=5.0), _active("b", used_percent=55.0)],
        "session_123",
        repo2,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_max_age_seconds=None,
        headroom_reallocate=True,
    )
    assert r2.account is not None
    assert r2.account.account_id == "b"
    repo2.delete.assert_not_called()










def _patch_audit_log():

    return patch.object(load_balancer_module.AuditService, "log_async", new_callable=Mock)


def _rebind_event(audit_log: Mock) -> dict:
    audit_log.assert_called_once()
    args, kwargs = audit_log.call_args
    assert args[0] == "sticky_session_rebound"
    return kwargs["details"]


@pytest.mark.asyncio
async def test_rebind_via_failover_records_rate_limited_failover():

    now = time.time()
    acc_a = _rate_limited("a", cooldown_until=now + 60)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    with _patch_audit_log() as audit_log:
        result = await _invoke_stickiness(
            [acc_a, acc_b],
            "session_123",
            repo,
            sticky_kind=StickySessionKind.CODEX_SESSION,
            reallocate_sticky=False,
            sticky_max_age_seconds=None,
        )

    assert result.account is not None
    assert result.account.account_id == "b"
    details = _rebind_event(audit_log)
    assert details["reason"] == "rate_limited_failover"
    assert details["sticky_kind"] == StickySessionKind.CODEX_SESSION.value
    assert details["old_account_id"] == "a"
    assert details["new_account_id"] == "b"

    assert details["sticky_key_hash"] != "session_123"
    assert len(details["sticky_key_hash"]) == 64


@pytest.mark.asyncio
async def test_rebind_via_budget_pressure_records_budget_pressure():

    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    with _patch_audit_log() as audit_log:
        result = await _invoke_stickiness(
            [acc_a, acc_b],
            "key1",
            repo,
            reallocate_sticky=False,
        )

    assert result.account is not None
    assert result.account.account_id == "b"
    details = _rebind_event(audit_log)
    assert details["reason"] == "budget_pressure"
    assert details["old_account_id"] == "a"
    assert details["new_account_id"] == "b"


@pytest.mark.asyncio
async def test_rebind_via_burn_first_drain_records_burn_first_drain():

    acc_a = _active("a", used_percent=50.0)
    acc_b = _active("b", used_percent=10.0, routing_policy="burn_first")
    repo = _make_sticky_repo(existing_account_id="a")

    with _patch_audit_log() as audit_log:
        result = await _invoke_stickiness(
            [acc_a, acc_b],
            "key1",
            repo,
            reallocate_sticky=False,
            burn_first_sticky_drain=True,
        )

    assert result.account is not None
    assert result.account.account_id == "b"
    details = _rebind_event(audit_log)
    assert details["reason"] == "burn_first_drain"
    assert details["old_account_id"] == "a"
    assert details["new_account_id"] == "b"


@pytest.mark.asyncio
async def test_rebind_via_paused_account_records_account_unavailable():

    acc_a = AccountState("a", AccountStatus.PAUSED)
    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    with _patch_audit_log() as audit_log:
        result = await _invoke_stickiness(
            [acc_a, acc_b],
            "key1",
            repo,
            reallocate_sticky=False,
        )

    assert result.account is not None
    assert result.account.account_id == "b"
    details = _rebind_event(audit_log)
    assert details["reason"] == "account_unavailable"


@pytest.mark.asyncio
async def test_rebind_when_pinned_account_left_pool_records_account_unavailable():

    acc_b = _active("b")
    repo = _make_sticky_repo(existing_account_id="a")

    with _patch_audit_log() as audit_log:
        result = await _invoke_stickiness(
            [acc_b],
            "key1",
            repo,
            reallocate_sticky=False,
        )

    assert result.account is not None
    assert result.account.account_id == "b"
    details = _rebind_event(audit_log)
    assert details["reason"] == "account_unavailable"
    assert details["old_account_id"] == "a"
    assert details["new_account_id"] == "b"


@pytest.mark.asyncio
async def test_same_account_repeat_selection_records_no_rebind():

    acc_a = _active("a", used_percent=10.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    with _patch_audit_log() as audit_log:
        result = await _invoke_stickiness(
            [acc_a, acc_b],
            "key1",
            repo,
            reallocate_sticky=False,
        )

    assert result.account is not None
    assert result.account.account_id == "a"
    audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_first_pin_creation_records_no_rebind():

    acc_a = _active("a", used_percent=10.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id=None)

    with _patch_audit_log() as audit_log:
        result = await _invoke_stickiness(
            [acc_a, acc_b],
            "key1",
            repo,
            reallocate_sticky=False,
        )

    assert result.account is not None
    assert result.account.account_id == "a"
    audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_rebind_recording_failure_does_not_break_selection():

    acc_a = _active("a", used_percent=96.0)
    acc_b = _active("b", used_percent=50.0)
    repo = _make_sticky_repo(existing_account_id="a")

    with _patch_audit_log() as audit_log:
        audit_log.side_effect = RuntimeError("simulated audit DB failure")
        result = await _invoke_stickiness(
            [acc_a, acc_b],
            "key1",
            repo,
            reallocate_sticky=False,
        )

    assert result.account is not None
    assert result.account.account_id == "b"
    audit_log.assert_called_once()

    repo.upsert.assert_called_once_with("key1", "b", kind=StickySessionKind.PROMPT_CACHE)
