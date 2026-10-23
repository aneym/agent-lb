from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import timedelta

import pytest

from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from tests.floor.token_refresh_claims_support import (
    _account_snapshot,
    _create_account,
    _rotated_result,
)


@pytest.mark.asyncio
async def test_refresh_persists_user_identity_and_access_expiry(db_setup, monkeypatch):
    account_id = "acc_refresh_metadata"
    await _create_account(account_id)
    started_at = utcnow()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        return replace(_rotated_result(account_id), chatgpt_user_id="user-refreshed", expires_in=3600)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        await AuthManager(repo).refresh_account(account)

    async with SessionLocal() as session:
        stored = await AccountsRepository(session).get_by_id(account_id)
        assert stored is not None
        assert stored.chatgpt_user_id == "user-refreshed"
        assert stored.access_expires_at is not None
        assert started_at + timedelta(hours=1) <= stored.access_expires_at <= utcnow() + timedelta(hours=1)


@pytest.mark.asyncio
async def test_rotate_tokens_cas_rejects_stale_writer(db_setup):
    account_id = "acc_cas"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        current_ciphertext = account.refresh_token_encrypted

        stale = await repo.rotate_tokens(
            account_id,
            access_token_encrypted=encryptor.encrypt("access-stale"),
            refresh_token_encrypted=encryptor.encrypt("refresh-stale"),
            id_token_encrypted=encryptor.encrypt("id-stale"),
            last_refresh=utcnow(),
            expected_refresh_token_encrypted=b"not-the-current-ciphertext",
        )
        assert stale is False

        applied = await repo.rotate_tokens(
            account_id,
            access_token_encrypted=encryptor.encrypt("access-new"),
            refresh_token_encrypted=encryptor.encrypt("refresh-new"),
            id_token_encrypted=encryptor.encrypt("id-new"),
            last_refresh=utcnow(),
            expected_refresh_token_encrypted=current_ciphertext,
        )
        assert applied is True

    _, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert stored_refresh_token == "refresh-new"


@pytest.mark.asyncio
async def test_rotate_tokens_requires_cas_predicate_no_unguarded_write(db_setup):
    """Root-enforcement regression (P1): the ONLY method that writes refresh-token
    ciphertext (``rotate_tokens``) makes the compare-and-set predicate mandatory.
    There is no keyword to omit it, so no caller can issue an unconditional token
    write, and a concurrent rotation always turns a stale writer into a guarded
    MISS (no clobber) rather than an unconditional overwrite."""
    signature = inspect.signature(AccountsRepository.rotate_tokens)
    cas_param = signature.parameters["expected_refresh_token_encrypted"]
    # Required (no default) and keyword-only: it cannot be dropped by any caller.
    assert cas_param.default is inspect.Parameter.empty
    assert cas_param.kind is inspect.Parameter.KEYWORD_ONLY

    account_id = "acc_rotate_guarded"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        # A stale writer (expected ciphertext no longer stored) is a no-op MISS.
        missed = await repo.rotate_tokens(
            account_id,
            access_token_encrypted=encryptor.encrypt("access-stale"),
            refresh_token_encrypted=encryptor.encrypt("refresh-stale"),
            id_token_encrypted=encryptor.encrypt("id-stale"),
            last_refresh=utcnow(),
            expected_refresh_token_encrypted=b"stale-ciphertext",
        )
        assert missed is False

    _, stored_refresh_token, _ = await _account_snapshot(account_id)
    # The concurrent (stored) material survives untouched.
    assert stored_refresh_token == "refresh-old"


@pytest.mark.asyncio
async def test_update_account_metadata_cannot_touch_token_material(db_setup):
    """Root-enforcement regression (P1): the metadata-only writer STRUCTURALLY
    cannot write token ciphertext (there is no parameter for it), so a
    metadata-only caller holding a stale ``Account`` snapshot can never clobber
    a concurrent refresh-token rotation. It persists only identity/plan/
    workspace fields while token material stays exactly as stored."""
    metadata_params = set(inspect.signature(AccountsRepository.update_account_metadata).parameters)
    # No token ciphertext parameter exists on the metadata writer.
    assert "refresh_token_encrypted" not in metadata_params
    assert "access_token_encrypted" not in metadata_params
    assert "id_token_encrypted" not in metadata_params

    account_id = "acc_metadata_only"
    await _create_account(account_id)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        applied = await repo.update_account_metadata(
            account_id,
            plan_type="pro",
            workspace_label="Renamed Workspace",
        )
        assert applied is True

    _, stored_refresh_token, _ = await _account_snapshot(account_id)
    # Token material is untouched by the metadata write.
    assert stored_refresh_token == "refresh-old"


@pytest.mark.asyncio
async def test_update_status_if_current_rejects_stale_refresh_token_material(db_setup):
    """The status CAS must also be conditioned on the refresh-token ciphertext
    so a permanent-failure downgrade cannot land over a concurrent rotation."""
    account_id = "acc_status_cas_material"
    await _create_account(account_id)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        current_ciphertext = account.refresh_token_encrypted

        stale = await repo.update_status_if_current(
            account_id,
            AccountStatus.REAUTH_REQUIRED,
            "stale permanent-failure write",
            expected_status=AccountStatus.ACTIVE,
            expected_refresh_token_encrypted=b"not-the-current-ciphertext",
        )
        assert stale is False
        status, _, sticky_present = await _account_snapshot(account_id)
        assert status == AccountStatus.ACTIVE
        assert sticky_present is True

        applied = await repo.update_status_if_current(
            account_id,
            AccountStatus.REAUTH_REQUIRED,
            "current permanent-failure write",
            expected_status=AccountStatus.ACTIVE,
            expected_refresh_token_encrypted=current_ciphertext,
        )
        assert applied is True

    status, _, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.REAUTH_REQUIRED


@pytest.mark.asyncio
async def test_permanent_failure_cas_loses_to_rotation_committed_during_status_write(db_setup, monkeypatch):
    """CAS race-window regression: a concurrent re-auth/import commits a token
    rotation AFTER the permanent-failure guard's fresh re-read but BEFORE its
    status CAS (status/reason/reset untouched, so the pre-hardening CAS
    matched). The stale REAUTH_REQUIRED write must lose and the freshly
    repaired account must stay active with the rotated material."""
    account_id = "acc_cas_race_window"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True, phase=ExchangePhase.ANSWERED)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    class _RaceWindowRepo(AccountsRepository):
        async def get_by_id_fresh(self, account_id: str) -> Account | None:
            latest = await super().get_by_id_fresh(account_id)
            # Concurrent re-auth commits a rotation through another session in
            # the window between this fresh read and the status CAS.
            async with SessionLocal() as winner_session:
                await AccountsRepository(winner_session).rotate_tokens(
                    account_id,
                    access_token_encrypted=encryptor.encrypt("access-rotated"),
                    refresh_token_encrypted=encryptor.encrypt("refresh-rotated"),
                    id_token_encrypted=encryptor.encrypt("id-rotated"),
                    last_refresh=utcnow(),
                    expected_refresh_token_encrypted=latest.refresh_token_encrypted if latest else b"",
                )
            return latest

    async with SessionLocal() as session:
        repo = _RaceWindowRepo(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo)

        # The permanent-failure guard re-reads inside the status-CAS retry,
        # observes that the peer committed genuinely different refresh-token
        # material, and adopts the repaired row instead of re-raising the
        # permanent RefreshError. Re-raising would let the proxy caller invoke
        # LoadBalancer.mark_permanent_failure() and clobber the valid rotation.
        refreshed = await manager.refresh_account(account)

    assert refreshed.status == AccountStatus.ACTIVE
    assert TokenEncryptor().decrypt(refreshed.refresh_token_encrypted) == "refresh-rotated"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-rotated"
    assert sticky_present is True


@pytest.mark.asyncio
async def test_permanent_failure_cas_retries_when_same_plaintext_re_encrypted(db_setup, monkeypatch):
    """Status-CAS retry regression: a concurrent re-auth/import re-encrypts the
    SAME refresh-token plaintext (non-deterministic Fernet) in the window
    between the permanent-failure guard's fresh re-read and its status CAS. The
    ciphertext guard misses even though there was no genuine rotation, and the
    account is still holding the very material that just failed permanently. The
    guard must re-read and retry the CAS against the freshly observed ciphertext
    and land the REAUTH_REQUIRED downgrade rather than skipping it and leaving a
    dead account active."""
    account_id = "acc_cas_reencrypt_retry"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True, phase=ExchangePhase.ANSWERED)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    reencrypted = {"done": False}

    class _ReEncryptWindowRepo(AccountsRepository):
        async def get_by_id_fresh(self, account_id: str) -> Account | None:
            latest = await super().get_by_id_fresh(account_id)
            if not reencrypted["done"]:
                reencrypted["done"] = True
                # Re-auth re-encrypts the SAME plaintext to different bytes in
                # the window between this fresh read and the status CAS. Status/
                # reason/reset are untouched, so only the ciphertext guard trips.
                async with SessionLocal() as reauth_session:
                    await AccountsRepository(reauth_session).rotate_tokens(
                        account_id,
                        access_token_encrypted=encryptor.encrypt("access-old"),
                        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
                        id_token_encrypted=encryptor.encrypt("id-old"),
                        last_refresh=utcnow(),
                        expected_refresh_token_encrypted=latest.refresh_token_encrypted if latest else b"",
                    )
            return latest

    async with SessionLocal() as session:
        repo = _ReEncryptWindowRepo(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo)

        with pytest.raises(RefreshError) as exc_info:
            await manager.refresh_account(account)

    assert exc_info.value.code == "refresh_token_reused"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    # The downgrade landed on retry: same (dead) plaintext, account de-routed.
    assert status == AccountStatus.REAUTH_REQUIRED
    assert stored_refresh_token == "refresh-old"
    assert sticky_present is False
