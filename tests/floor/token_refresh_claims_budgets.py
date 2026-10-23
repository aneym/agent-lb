from __future__ import annotations

import asyncio

import pytest

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.db.models import AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.refresh_claims import RefreshClaimCoordinator
from app.modules.accounts.repository import AccountsRepository
from tests.floor.token_refresh_claims_support import (
    _account_snapshot,
    _create_account,
    _insert_claim,
    _rotated_result,
)


@pytest.mark.asyncio
async def test_claim_wait_then_exchange_caps_to_remaining_budget(db_setup, monkeypatch):
    """Budget recompute after a claim wait: when the winner acquires the claim
    only after waiting out a foreign claim, it MUST cap the upstream OAuth
    exchange to the caller's REMAINING budget rather than restarting with the
    original full budget. The singleflight body is shielded from caller
    cancellation, so a full-budget exchange after a full-budget wait would
    overrun the request deadline while pinning the repo session and singleflight
    entry."""
    from app.core.auth.refresh import (
        get_token_refresh_timeout_override,
        pop_token_refresh_timeout_override,
        push_token_refresh_timeout_override,
    )

    account_id = "acc_claim_budget_cap"
    await _create_account(account_id)

    observed_override: list[float | None] = []

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        observed_override.append(get_token_refresh_timeout_override())
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    class _WaitThenWinClaims:
        @property
        def claimant_id(self) -> str:
            return "replica-slow-win"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del account_id, ttl_seconds, owner
            # Simulate waiting out a foreign claim that then releases: consume a
            # chunk of the caller budget before this replica wins the claim.
            await asyncio.sleep(0.5)
            return True

        async def release(self, account_id: str, *, owner: str) -> None:
            del account_id, owner

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=_WaitThenWinClaims())

        token = push_token_refresh_timeout_override(5.0)
        try:
            result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)
        finally:
            pop_token_refresh_timeout_override(token)

    assert TokenEncryptor().decrypt(result.refresh_token_encrypted) == "refresh-new"
    # The exchange ran exactly once and saw a capped override well below the
    # original 5.0s budget (roughly the remaining ~4.5s), never the full budget.
    assert len(observed_override) == 1
    capped = observed_override[0]
    assert capped is not None
    assert 0.0 < capped < 4.9


@pytest.mark.asyncio
async def test_claim_wait_exhausting_budget_fails_fast_without_exchange(db_setup, monkeypatch):
    """Fail fast when the claim wait consumes the whole caller budget: the
    winner MUST raise the transient (non-permanent) claim-timeout error before
    starting the upstream exchange rather than launching a full-budget OAuth
    exchange that overruns the request deadline."""
    from app.core.auth.refresh import (
        pop_token_refresh_timeout_override,
        push_token_refresh_timeout_override,
    )

    account_id = "acc_claim_budget_exhausted"
    await _create_account(account_id)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    released: list[str] = []

    class _WaitPastBudgetClaims:
        @property
        def claimant_id(self) -> str:
            return "replica-late-win"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del ttl_seconds, owner
            # Wait longer than the 0.2s caller budget before winning the claim.
            await asyncio.sleep(0.3)
            del account_id
            return True

        async def release(self, account_id: str, *, owner: str) -> None:
            del owner
            released.append(account_id)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=_WaitPastBudgetClaims())

        token = push_token_refresh_timeout_override(0.2)
        try:
            with pytest.raises(RefreshError) as exc_info:
                await asyncio.wait_for(manager.refresh_account(account), timeout=5)
        finally:
            pop_token_refresh_timeout_override(token)

    assert exc_info.value.code == "refresh_claim_timeout"
    assert exc_info.value.is_permanent is False
    assert exc_info.value.transport_error is True
    # No upstream exchange ran, and the claim we won was released on the way out.
    assert upstream_calls == 0
    assert released == [account_id]
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-old"


@pytest.mark.asyncio
async def test_claim_poll_sleep_capped_to_remaining_caller_budget(db_setup, monkeypatch):
    """Per-iteration poll sleep must be bounded by the caller budget.

    With a live foreign claim the loser polls until its bounded wait elapses.
    When the caller supplies a refresh budget SMALLER than the configured poll
    interval, each poll sleep must be capped to what remains of the caller
    deadline (``min(poll_interval, remaining)``) instead of sleeping the full
    interval. The singleflight body is shielded from caller cancellation, so a
    full-interval sleep would overrun the caller budget while holding the repo
    session and the inflight singleflight entry that later callers join.

    Before the fix the loop slept the full ~poll interval each iteration, so a
    0.1s caller budget with a 3.0s poll interval blocked for ~3.0s before the
    transient claim-timeout surfaced. The fix bounds the wait to the budget.
    """
    import time

    from app.core.auth.refresh import (
        pop_token_refresh_timeout_override,
        push_token_refresh_timeout_override,
    )

    # Claim-wait cap and poll interval both far exceed the caller budget so the
    # only thing that can bound the wait is the per-iteration budget cap.
    monkeypatch.setattr(auth_manager_module, "_TOKEN_REFRESH_CLAIM_WAIT_SECONDS", 5.0)
    monkeypatch.setattr(auth_manager_module, "_TOKEN_REFRESH_CLAIM_POLL_SECONDS", 3.0)

    account_id = "acc_claim_poll_budget_cap"
    await _create_account(account_id)
    await _insert_claim(account_id, claimed_by="other-replica", expires_in_seconds=60)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))

        token = push_token_refresh_timeout_override(0.1)
        started = time.monotonic()
        try:
            with pytest.raises(RefreshError) as exc_info:
                await asyncio.wait_for(manager.ensure_fresh(account, force=True), timeout=5)
        finally:
            pop_token_refresh_timeout_override(token)
        elapsed = time.monotonic() - started

    assert exc_info.value.code == "refresh_claim_timeout"
    assert exc_info.value.is_permanent is False
    assert exc_info.value.transport_error is True
    assert upstream_calls == 0
    # The wait was bounded by the ~0.1s caller budget, NOT the 3.0s poll
    # interval. A generous ceiling keeps this deterministic under load while
    # still failing loudly if the full poll interval is ever slept.
    assert elapsed < 1.5, f"claim poll overran caller budget: {elapsed:.3f}s"
    # The foreign claim was never won, so account material is untouched.
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-old"


@pytest.mark.asyncio
async def test_saturated_admission_fails_fast_and_releases_claim_within_budget(db_setup, monkeypatch):
    """Admission wait must be bounded by the caller budget while the claim is held.

    The claim winner acquires token-refresh admission BEFORE the upstream OAuth
    exchange. ``WorkAdmissionController`` waits up to
    ``admission_wait_timeout_seconds`` for a slot on a saturated token-refresh
    semaphore, and that wait happens while this shielded task already holds the
    cross-replica DB refresh claim. Without capping the admission wait by the
    caller budget, a small-budget request would hold the claim — blocking peer
    replicas on the same account — for the full admission timeout.

    Here the single token-refresh admission slot is pre-held (saturated) and the
    admission wait timeout is a full 10s, but the caller budget is only 0.2s. The
    refresh MUST fail fast with the transient (non-permanent) claim-timeout,
    release the claim, and never start the exchange — all within roughly the
    caller budget, NOT the 10s admission timeout.
    """
    import time

    from app.core.auth.refresh import (
        pop_token_refresh_timeout_override,
        push_token_refresh_timeout_override,
    )
    from app.modules.proxy.work_admission import WorkAdmissionController

    account_id = "acc_claim_admission_saturated"
    await _create_account(account_id)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    released: list[str] = []

    class _ImmediateWinClaims:
        @property
        def claimant_id(self) -> str:
            return "replica-admission"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del account_id, ttl_seconds, owner
            return True

        async def release(self, account_id: str, *, owner: str) -> None:
            del owner
            released.append(account_id)

    # One admission slot, held so the gate is saturated; a generous 10s wait
    # timeout so ONLY the caller budget can bound the admission wait.
    controller = WorkAdmissionController(
        token_refresh_limit=1,
        websocket_connect_limit=0,
        response_create_limit=0,
        compact_response_create_limit=0,
        admission_wait_timeout_seconds=10.0,
    )
    held_slot = await controller.acquire_token_refresh()
    try:
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            account = await repo.get_by_id(account_id)
            assert account is not None
            manager = AuthManager(
                repo,
                acquire_refresh_admission=controller.acquire_token_refresh,
                refresh_claims=_ImmediateWinClaims(),
            )

            token = push_token_refresh_timeout_override(0.2)
            started = time.monotonic()
            try:
                with pytest.raises(RefreshError) as exc_info:
                    await asyncio.wait_for(manager.refresh_account(account), timeout=5)
            finally:
                pop_token_refresh_timeout_override(token)
            elapsed = time.monotonic() - started
    finally:
        held_slot.release()

    assert exc_info.value.code == "refresh_claim_timeout"
    assert exc_info.value.is_permanent is False
    assert exc_info.value.transport_error is True
    # No exchange ran, and the DB refresh claim was released on the way out
    # (rather than held for the full 10s admission timeout, which would block
    # peer replicas on this account's claim).
    assert upstream_calls == 0
    assert released == [account_id]
    # Bounded by the ~0.2s budget, NOT the 10s admission wait timeout. A generous
    # ceiling keeps this deterministic under load while still failing loudly if
    # the full admission timeout is ever waited while holding the claim.
    assert elapsed < 2.0, f"admission wait overran caller budget while holding claim: {elapsed:.3f}s"
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-old"
