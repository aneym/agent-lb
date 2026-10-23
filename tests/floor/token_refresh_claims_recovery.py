from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountRefreshClaim, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AccountNotOwnedError, AuthManager
from app.modules.accounts.refresh_claims import RefreshClaimCoordinator
from app.modules.accounts.repository import AccountsRepository
from tests.floor.token_refresh_claims_support import (
    _account_snapshot,
    _commit_peer_reauth,
    _commit_terminal_status,
    _create_account,
)


@pytest.mark.asyncio
async def test_claim_winner_rejects_rotation_transferred_to_foreign_custody(db_setup):
    account_id = "acc_claim_foreign_custody"
    await _create_account(account_id)
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        async with SessionLocal() as transfer_session:
            transferred = await transfer_session.get(Account, account_id)
            assert transferred is not None
            transferred.refresh_token_encrypted = encryptor.encrypt("refresh-transferred")
            transferred.owner_instance = "foreign-custody-owner"
            await transfer_session.commit()

        claims = RefreshClaimCoordinator(claimant_id="replica-a")
        with pytest.raises(AccountNotOwnedError):
            await AuthManager(repo, refresh_claims=claims).refresh_account(account)
        assert await claims.current_claim(account_id) is None


@pytest.mark.asyncio
async def test_winner_adopts_rotation_committed_before_its_claim(db_setup, monkeypatch):
    """Post-claim fresh re-read: when the material already rotated, the claim
    winner must adopt it with zero upstream calls."""
    account_id = "acc_claim_preclaim_rotation"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def unexpected_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise AssertionError("upstream exchange must not run when the material already rotated")

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", unexpected_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None

        # Another replica rotates and commits after our snapshot was taken.
        async with SessionLocal() as winner_session:
            await AccountsRepository(winner_session).rotate_tokens(
                account_id,
                access_token_encrypted=encryptor.encrypt("access-new"),
                refresh_token_encrypted=encryptor.encrypt("refresh-new"),
                id_token_encrypted=encryptor.encrypt("id-new"),
                last_refresh=utcnow(),
                expected_refresh_token_encrypted=account.refresh_token_encrypted,
            )

        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)

    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, _, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_winner_honors_prior_holder_permanent_reauth_on_unchanged_token(db_setup, monkeypatch):
    """Fail-closed regression: a PRIOR claim holder ran the exchange, received a
    permanent ``invalid_grant``, and flagged the account REAUTH_REQUIRED WITHOUT
    rotating the refresh token. A waiter that subsequently wins the released
    claim re-reads the SAME consumed token; before this fix it re-exchanged that
    dead token (second upstream call) and generated another permanent failure.
    It must instead honor the terminal status and surface it as a permanent
    failure with NO second upstream exchange."""
    account_id = "acc_terminal_reauth_permanent"
    await _create_account(account_id)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        # The single-use refresh token is permanently invalid upstream.
        raise RefreshError("invalid_grant", "refresh token grant invalid", True, phase=ExchangePhase.ANSWERED)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as holder_session, SessionLocal() as waiter_session:
        holder_repo = AccountsRepository(holder_session)
        waiter_repo = AccountsRepository(waiter_session)
        holder_account = await holder_repo.get_by_id(account_id)
        waiter_account = await waiter_repo.get_by_id(account_id)
        assert holder_account is not None and waiter_account is not None

        # PRIOR holder: wins the claim, receives a permanent failure, commits
        # REAUTH_REQUIRED (token unchanged), then releases the claim.
        holder = AuthManager(holder_repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        with pytest.raises(RefreshError) as holder_exc:
            await holder.refresh_account(holder_account)
        assert holder_exc.value.is_permanent

        # WAITER wins the RELEASED claim with a STALE (still-ACTIVE) snapshot of
        # the same token; it must fail closed rather than re-exchange.
        waiter = AuthManager(waiter_repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))
        with pytest.raises(RefreshError) as waiter_exc:
            await waiter.refresh_account(waiter_account)

    assert waiter_exc.value.is_permanent
    assert not waiter_exc.value.transport_error
    # 1, NOT 2: the waiter never re-exchanged the dead single-use token.
    assert upstream_calls == 1
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.REAUTH_REQUIRED
    assert stored_refresh_token == "refresh-old"
    async with SessionLocal() as session:
        remaining = (
            await session.execute(select(AccountRefreshClaim).where(AccountRefreshClaim.account_id == account_id))
        ).scalar_one_or_none()
        assert remaining is None


@pytest.mark.asyncio
async def test_winner_honors_persist_conflict_reauth_on_unchanged_token(db_setup, monkeypatch):
    """Same fail-closed guard for the safe-terminal persist-conflict path: a
    prior holder that exhausted its dedicated final-persist retries flags
    REAUTH_REQUIRED while the CONSUMED token is still stored (unchanged). A
    waiter that wins the released claim must NOT exchange that consumed token
    again; it surfaces the terminal state as a permanent failure."""
    account_id = "acc_terminal_reauth_persist_conflict"
    await _create_account(account_id)
    # The committed outcome of ``_flag_persist_conflict_reauth``: REAUTH_REQUIRED
    # with the consumed token still stored.
    await _commit_terminal_status(
        account_id,
        status=AccountStatus.REAUTH_REQUIRED,
        reason="Refresh token persistence conflict; stored token is stale - re-login required",
    )

    async def unexpected_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise AssertionError("waiter must not exchange the consumed token after a peer flagged reauth")

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", unexpected_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))
        with pytest.raises(RefreshError) as waiter_exc:
            await manager.refresh_account(account)

    assert waiter_exc.value.is_permanent
    assert not waiter_exc.value.transport_error
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.REAUTH_REQUIRED
    assert stored_refresh_token == "refresh-old"


@pytest.mark.asyncio
async def test_winner_adopts_peer_rotation_that_repaired_a_reauth_account(db_setup, monkeypatch):
    """A prior holder flagged REAUTH_REQUIRED, but a peer then genuinely
    re-authenticated and ROTATED the refresh token (fingerprint changed). The
    waiter must ADOPT the repaired rotation and proceed with zero upstream calls
    — a repaired account is NOT treated as terminal. The fingerprint-differs
    branch takes precedence over the terminal-status check."""
    account_id = "acc_reauth_then_peer_rotation"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def unexpected_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise AssertionError("waiter must adopt the peer rotation without an upstream call")

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", unexpected_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None

        # A prior holder flagged the account for reauth on the OLD token...
        await _commit_terminal_status(
            account_id,
            status=AccountStatus.REAUTH_REQUIRED,
            reason="Refresh token grant invalid - re-login required",
        )
        # ...then a peer genuinely re-authenticated: rotated the token and cleared
        # the terminal status back to ACTIVE.
        await _commit_peer_reauth(account_id, refresh_token="refresh-new")

        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))
        result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)

    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"


@pytest.mark.asyncio
async def test_permanent_failure_guard_sees_committed_rotation_despite_identity_map(db_setup, monkeypatch):
    """Stale-guard regression (failed pre-hardening): the loser's session
    identity map still holds the pre-rotation row; ``session.get`` returned it
    without a DB read and the loser wrote REAUTH_REQUIRED over a healthy
    account. The fresh ``populate_existing`` re-read must observe the winner's
    committed rotation and adopt it instead."""
    account_id = "acc_stale_guard"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True, phase=ExchangePhase.ANSWERED)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as loser_session:
        loser_repo = AccountsRepository(loser_session)
        # Populate the loser session's identity map with the pre-rotation row.
        loser_account = await loser_repo.get_by_id(account_id)
        assert loser_account is not None

        # Winner commits the rotation through a different session (replica).
        async with SessionLocal() as winner_session:
            await AccountsRepository(winner_session).rotate_tokens(
                account_id,
                access_token_encrypted=encryptor.encrypt("access-new"),
                refresh_token_encrypted=encryptor.encrypt("refresh-new"),
                id_token_encrypted=encryptor.encrypt("id-new"),
                last_refresh=utcnow(),
                expected_refresh_token_encrypted=loser_account.refresh_token_encrypted,
            )

        # Exercise the legacy (unclaimed) path: the hardening must protect
        # callers even without a claim coordinator.
        manager = AuthManager(loser_repo)
        result = await manager.refresh_account(loser_account)

    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"
    assert sticky_present is True
