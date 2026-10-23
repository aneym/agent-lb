from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.db.models import AccountRefreshClaim, AccountStatus
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
async def test_concurrent_cross_replica_refresh_runs_one_upstream_exchange(db_setup, monkeypatch):
    """THE RACE (failed pre-claims): both replicas force-refresh the same
    account; pre-claims the loser POSTed the same single-use refresh token,
    received permanent ``refresh_token_reused``, wrote REAUTH_REQUIRED, and
    deleted the sticky session. With claims, exactly one upstream exchange runs
    and the loser adopts the winner's rotation."""
    account_id = "acc_claim_race"
    await _create_account(account_id)

    upstream_calls = 0
    winner_started = asyncio.Event()
    winner_release = asyncio.Event()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        if upstream_calls > 1:
            # This is exactly what upstream returns to the loser of a
            # concurrent rotation of a single-use refresh token.
            raise RefreshError("refresh_token_reused", "refresh token reused", True, phase=ExchangePhase.ANSWERED)
        winner_started.set()
        await winner_release.wait()
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session_a, SessionLocal() as session_b:
        repo_a = AccountsRepository(session_a)
        repo_b = AccountsRepository(session_b)
        account_a = await repo_a.get_by_id(account_id)
        account_b = await repo_b.get_by_id(account_id)
        assert account_a is not None and account_b is not None
        manager_a = AuthManager(repo_a, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        manager_b = AuthManager(repo_b, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))

        task_a = asyncio.create_task(manager_a.refresh_account(account_a))
        await asyncio.wait_for(winner_started.wait(), timeout=5)
        # Replica A holds the claim and is mid-exchange; replica B must wait.
        task_b = asyncio.create_task(manager_b.refresh_account(account_b))
        await asyncio.sleep(0.1)
        assert not task_b.done()

        winner_release.set()
        result_a = await asyncio.wait_for(task_a, timeout=5)
        result_b = await asyncio.wait_for(task_b, timeout=5)

    encryptor = TokenEncryptor()
    assert upstream_calls == 1
    assert encryptor.decrypt(result_a.refresh_token_encrypted) == "refresh-new"
    assert encryptor.decrypt(result_b.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"
    assert sticky_present is True

    # The claim was released after the winner persisted.
    async with SessionLocal() as session:
        remaining = (
            await session.execute(select(AccountRefreshClaim).where(AccountRefreshClaim.account_id == account_id))
        ).scalar_one_or_none()
        assert remaining is None


@pytest.mark.asyncio
async def test_expired_foreign_claim_is_taken_over(db_setup, monkeypatch):
    """Crashed-claimant liveness: an expired foreign claim must not block refresh."""
    account_id = "acc_claim_expired"
    await _create_account(account_id)
    await _insert_claim(account_id, claimed_by="dead-replica", expires_in_seconds=-5)

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)

    assert TokenEncryptor().decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"


@pytest.mark.asyncio
async def test_unexpired_foreign_claim_times_out_transient_and_is_not_cached(db_setup, monkeypatch):
    """Bounded wait: with a live foreign claim the loser must fail with a
    transient (non-permanent) error, never call upstream, never touch account
    status, and the singleflight must not cache the failure as permanent."""
    monkeypatch.setattr(auth_manager_module, "_TOKEN_REFRESH_CLAIM_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(auth_manager_module, "_TOKEN_REFRESH_CLAIM_POLL_SECONDS", 0.05)

    account_id = "acc_claim_blocked"
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

        with pytest.raises(RefreshError) as exc_info:
            await manager.ensure_fresh(account, force=True)
        assert exc_info.value.code == "refresh_claim_timeout"
        assert exc_info.value.is_permanent is False
        assert exc_info.value.transport_error is True
        assert upstream_calls == 0

        status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
        assert status == AccountStatus.ACTIVE
        assert stored_refresh_token == "refresh-old"
        assert sticky_present is True

        # Release the foreign claim: the next forced refresh must proceed
        # immediately. A cached permanent failure would re-raise instead.
        async with SessionLocal() as cleanup_session:
            claim = (
                await cleanup_session.execute(
                    select(AccountRefreshClaim).where(AccountRefreshClaim.account_id == account_id)
                )
            ).scalar_one()
            await cleanup_session.delete(claim)
            await cleanup_session.commit()

        refreshed = await asyncio.wait_for(manager.ensure_fresh(account, force=True), timeout=5)
        assert upstream_calls == 1
        assert TokenEncryptor().decrypt(refreshed.refresh_token_encrypted) == "refresh-new"
