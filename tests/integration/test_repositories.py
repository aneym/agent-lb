from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
)
from app.db.session import SessionLocal
from app.modules.accounts.repository import (
    AccountIdentityConflictError,
    AccountsRepository,
    _slot_lock_key,
    _slot_lock_keys,
)
from app.modules.request_logs.repository import RequestLogsRepository
from tests.integration.repository_account_support import _make_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_accounts_upsert_updates_existing_by_email(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc1", "dup@example.com"))

        updated = _make_account("acc2", "dup@example.com")
        updated.plan_type = "team"
        updated.status = AccountStatus.PAUSED
        updated.deactivation_reason = "reauth"
        await repo.upsert(updated)

        result = await session.execute(select(Account).where(Account.email == "dup@example.com"))
        stored = result.scalar_one()
        assert stored.id == "acc1"
        assert stored.plan_type == "team"
        assert stored.status == AccountStatus.PAUSED
        assert stored.deactivation_reason == "reauth"

        all_accounts = await session.execute(select(Account))
        assert len(list(all_accounts.scalars().all())) == 1


@pytest.mark.asyncio
async def test_account_reload_and_token_update_compare_and_swap(db_setup):
    encryptor = TokenEncryptor()
    original = _make_account("acc_refresh_cas", "refresh-cas@example.com")

    async with SessionLocal() as stale_session:
        stale_repo = AccountsRepository(stale_session)
        cached = await stale_repo.upsert(original, merge_by_email=False)
        original_refresh_token_encrypted = cached.refresh_token_encrypted

        async with SessionLocal() as winner_session:
            winner_repo = AccountsRepository(winner_session)
            winner_updated = await winner_repo.update_tokens(
                cached.id,
                access_token_encrypted=encryptor.encrypt("winning-access"),
                refresh_token_encrypted=encryptor.encrypt("winning-refresh"),
                id_token_encrypted=encryptor.encrypt("winning-id"),
                last_refresh=utcnow(),
                expected_refresh_token_encrypted=original_refresh_token_encrypted,
            )

        assert winner_updated is True
        assert encryptor.decrypt(cached.refresh_token_encrypted) == "refresh"
        assert await stale_repo.get_by_id(cached.id) is cached

        reloaded = await stale_repo.reload_by_id(cached.id)
        assert reloaded is cached
        assert encryptor.decrypt(reloaded.refresh_token_encrypted) == "winning-refresh"

        reloaded.email = "stale-pending@example.com"
        losing_update = await stale_repo.update_tokens(
            cached.id,
            access_token_encrypted=encryptor.encrypt("losing-access"),
            refresh_token_encrypted=encryptor.encrypt("losing-refresh"),
            id_token_encrypted=encryptor.encrypt("losing-id"),
            last_refresh=utcnow(),
            expected_refresh_token_encrypted=original_refresh_token_encrypted,
        )

        assert losing_update is False

    async with SessionLocal() as verify_session:
        stored = await AccountsRepository(verify_session).get_by_id("acc_refresh_cas")
        assert stored is not None
        assert stored.email == "refresh-cas@example.com"
        assert encryptor.decrypt(stored.access_token_encrypted) == "winning-access"
        assert encryptor.decrypt(stored.refresh_token_encrypted) == "winning-refresh"


@pytest.mark.asyncio
async def test_accounts_upsert_with_merge_disabled_keeps_duplicate_identity(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        first = await repo.upsert(_make_account("acc_same", "dup@example.com"), merge_by_email=False)

        updated = _make_account("acc_same", "dup@example.com")
        updated.plan_type = "team"
        second = await repo.upsert(updated, merge_by_email=False)

        assert first.id == "acc_same"
        assert second.id != first.id
        assert second.id.startswith("acc_same__copy")

        result = await session.execute(select(Account).where(Account.email == "dup@example.com"))
        rows = list(result.scalars().all())
        assert len(rows) == 2
        row_ids = {row.id for row in rows}
        assert first.id in row_ids
        assert second.id in row_ids


@pytest.mark.asyncio
async def test_accounts_upsert_reauthorized_heals_deactivated_identity_even_when_merge_disabled(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        existing = _make_account("acc_reauth", "reauth@example.com")
        existing.status = AccountStatus.DEACTIVATED
        existing.deactivation_reason = "refresh_failed"
        existing.routing_policy = "preserve"
        await repo.upsert(existing, merge_by_email=False)

        incoming = _make_account("acc_reauth", "reauth@example.com")
        incoming.plan_type = "team"
        saved = await repo.upsert_reauthorized(incoming)

        assert saved.id == "acc_reauth"
        assert saved.status == AccountStatus.ACTIVE
        assert saved.deactivation_reason is None
        assert saved.plan_type == "team"
        assert saved.routing_policy == "preserve"

        result = await session.execute(select(Account).where(Account.email == "reauth@example.com"))
        rows = list(result.scalars().all())
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_accounts_upsert_with_merge_enabled_raises_conflict_on_ambiguous_email(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_same", "dup@example.com"), merge_by_email=False)
        await repo.upsert(_make_account("acc_same", "dup@example.com"), merge_by_email=False)

        incoming = _make_account("acc_new", "dup@example.com")
        with pytest.raises(AccountIdentityConflictError):
            await repo.upsert(incoming, merge_by_email=True)


@pytest.mark.asyncio
async def test_accounts_upsert_with_merge_enabled_serializes_concurrent_same_email(db_setup):
    email = "race@example.com"
    barrier = asyncio.Barrier(2)

    async def _worker(account_id: str, plan_type: str) -> str:
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            await barrier.wait()
            incoming = _make_account(account_id, email)
            incoming.plan_type = plan_type
            saved = await repo.upsert(incoming, merge_by_email=True)
            return saved.id

    first_id, second_id = await asyncio.gather(
        _worker("acc_race_a", "plus"),
        _worker("acc_race_b", "team"),
    )

    assert first_id in {"acc_race_a", "acc_race_b"}
    assert second_id in {"acc_race_a", "acc_race_b"}

    async with SessionLocal() as session:
        result = await session.execute(select(Account).where(Account.email == email))
        rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].id in {"acc_race_a", "acc_race_b"}
        assert rows[0].plan_type in {"plus", "team"}


@pytest.mark.asyncio
async def test_accounts_upsert_with_merge_disabled_uses_identity_lock_on_postgresql(db_setup, monkeypatch):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        acquired_identity_locks: list[str] = []

        monkeypatch.setattr(repo, "_dialect_name", lambda: "postgresql")

        async def _record_identity_lock(account_id: str) -> None:
            acquired_identity_locks.append(account_id)

        async def _fail_merge_lock(_: str) -> None:
            raise AssertionError("merge lock should not be used when merge_by_email is disabled")

        monkeypatch.setattr(repo, "_acquire_postgresql_identity_lock", _record_identity_lock)
        monkeypatch.setattr(repo, "_acquire_postgresql_merge_lock", _fail_merge_lock)

        await repo.upsert(_make_account("acc_non_merge_lock", "non-merge-lock@example.com"), merge_by_email=False)

        assert acquired_identity_locks == ["acc_non_merge_lock"]


def test_slot_lock_key_serializes_unknown_workspace_overwrite_by_email():
    first = _make_account("acc_unknown_a", "unknown-workspace@example.com")
    second = _make_account("acc_unknown_b", "unknown-workspace@example.com")

    assert _slot_lock_key(first, preserve_unknown_workspace_duplicates=False) == _slot_lock_key(
        second,
        preserve_unknown_workspace_duplicates=False,
    )
    assert _slot_lock_key(first, preserve_unknown_workspace_duplicates=True) != _slot_lock_key(
        second,
        preserve_unknown_workspace_duplicates=True,
    )


def test_slot_lock_key_serializes_email_workspace_slot_when_account_id_appears_later():
    email_only = _make_account("acc_generated", "late-id@example.com")
    email_only.workspace_id = "ws_late"
    with_account_id = _make_account("acc_raw", "late-id@example.com")
    with_account_id.chatgpt_account_id = "raw_account_id"
    with_account_id.workspace_id = "ws_late"

    assert set(_slot_lock_keys(email_only)) & set(_slot_lock_keys(with_account_id)) == {
        "slot-email:late-id@example.com:ws_late",
    }


def test_slot_lock_key_serializes_legacy_unknown_workspace_upgrade():
    legacy = _make_account("acc_legacy", "legacy-workspace@example.com")
    upgraded = _make_account("acc_upgraded", "legacy-workspace@example.com")
    upgraded.chatgpt_account_id = "raw_account_id"
    upgraded.workspace_id = "ws_late"

    assert set(_slot_lock_keys(legacy, preserve_unknown_workspace_duplicates=False)) & set(
        _slot_lock_keys(upgraded, preserve_unknown_workspace_duplicates=False)
    ) == {"slot-email-unknown:legacy-workspace@example.com"}


def test_slot_lock_key_serializes_account_workspace_slot_when_email_changes():
    old_email = _make_account("acc_generated_old", "old-email@example.com")
    old_email.chatgpt_account_id = "raw_account_id"
    old_email.workspace_id = "ws_late"
    new_email = _make_account("acc_generated_new", "new-email@example.com")
    new_email.chatgpt_account_id = "raw_account_id"
    new_email.workspace_id = "ws_late"

    assert set(_slot_lock_keys(old_email)) & set(_slot_lock_keys(new_email)) == {
        "slot:raw_account_id:ws_late",
    }


@pytest.mark.asyncio
async def test_account_slot_upgrades_single_legacy_unknown_workspace_row(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        legacy = await repo.upsert_account_slot(
            _make_account("acc_legacy_workspace", "legacy-workspace-row@example.com"),
            preserve_unknown_workspace_duplicates=False,
        )
        await RequestLogsRepository(session).add_log(
            account_id=legacy.id,
            request_id="req_legacy_workspace",
            model="gpt-5.1",
            input_tokens=7,
            output_tokens=11,
            latency_ms=50,
            status="ok",
            error_code=None,
        )

        upgraded = _make_account("acc_raw_workspace", "legacy-workspace-row@example.com")
        upgraded.chatgpt_account_id = "raw_workspace_account"
        upgraded.workspace_id = "ws_legacy_team"
        upgraded.workspace_label = "Legacy Team"
        upgraded.plan_type = "team"

        stored = await repo.upsert_account_slot(upgraded, preserve_unknown_workspace_duplicates=False)

        assert stored.id == legacy.id
        assert stored.chatgpt_account_id == "raw_workspace_account"
        assert stored.workspace_id == "ws_legacy_team"
        assert stored.workspace_label == "Legacy Team"
        assert stored.plan_type == "team"

        accounts = list((await session.execute(select(Account))).scalars().all())
        assert [account.id for account in accounts] == [legacy.id]

        usage = await repo.list_request_usage_summary_by_account([stored.id])
        assert usage[stored.id].request_count == 1


@pytest.mark.asyncio
async def test_workspace_slot_taken_ignores_same_email_workspace_when_chatgpt_identity_differs(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        first = _make_account("acc_first_slot", "slot-taken@example.com")
        first.chatgpt_account_id = "raw_first_slot"
        first.workspace_id = "ws_shared_slot"
        await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)

        assert (
            await repo.workspace_slot_taken(
                account_id="acc_second_slot",
                email="slot-taken@example.com",
                chatgpt_account_id="raw_second_slot",
                workspace_id="ws_shared_slot",
            )
            is False
        )
        assert (
            await repo.workspace_slot_taken(
                account_id="acc_second_slot",
                email="slot-taken@example.com",
                chatgpt_account_id="raw_first_slot",
                workspace_id="ws_shared_slot",
            )
            is True
        )
        assert (
            await repo.workspace_slot_taken(
                account_id="acc_second_slot",
                email="slot-taken@example.com",
                chatgpt_account_id=None,
                workspace_id="ws_shared_slot",
            )
            is True
        )


@pytest.mark.asyncio
async def test_account_slot_keeps_distinct_workspace_chatgpt_identities(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        first = _make_account("acc_first", "shared-workspace@example.com")
        first.chatgpt_account_id = "raw_first"
        first.workspace_id = "ws_shared"
        await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)

        second = _make_account("acc_second", "shared-workspace@example.com")
        second.chatgpt_account_id = "raw_second"
        second.workspace_id = "ws_shared"
        saved = await repo.upsert_account_slot(second, preserve_unknown_workspace_duplicates=False)

        assert saved.id == "acc_second"

        accounts = list((await session.execute(select(Account).order_by(Account.id.asc()))).scalars().all())
        assert [(account.id, account.chatgpt_account_id) for account in accounts] == [
            ("acc_first", "raw_first"),
            ("acc_second", "raw_second"),
        ]


@pytest.mark.asyncio
async def test_account_slot_preserves_known_chatgpt_identity_on_email_fallback(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        known = _make_account("acc_known", "known-workspace@example.com")
        known.chatgpt_account_id = "raw_known"
        known.workspace_id = "ws_known"
        known.workspace_label = "Known Team"
        await repo.upsert_account_slot(known, preserve_unknown_workspace_duplicates=False)

        imported = _make_account("acc_imported", "known-workspace@example.com")
        imported.workspace_id = "ws_known"
        imported.workspace_label = "Known Team Updated"
        imported.plan_type = "team"
        saved = await repo.upsert_account_slot(imported, preserve_unknown_workspace_duplicates=False)

        assert saved.id == "acc_known"
        assert saved.chatgpt_account_id == "raw_known"
        assert saved.workspace_id == "ws_known"
        assert saved.workspace_label == "Known Team Updated"
        assert saved.plan_type == "team"

        accounts = list((await session.execute(select(Account))).scalars().all())
        assert [(account.id, account.chatgpt_account_id) for account in accounts] == [("acc_known", "raw_known")]


@pytest.mark.asyncio
async def test_account_slot_does_not_promote_mismatched_legacy_row_by_email(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        legacy = _make_account("acc_legacy", "legacy-shared@example.com")
        legacy.chatgpt_account_id = "raw_legacy"
        await repo.upsert(legacy, merge_by_email=False)

        workspace = _make_account("acc_workspace", "legacy-shared@example.com")
        workspace.chatgpt_account_id = "raw_workspace"
        workspace.workspace_id = "ws_new"
        saved = await repo.upsert_account_slot(workspace, preserve_unknown_workspace_duplicates=False)

        assert saved.id == "acc_workspace"

        accounts = list((await session.execute(select(Account).order_by(Account.id.asc()))).scalars().all())
        assert [(account.id, account.chatgpt_account_id, account.workspace_id) for account in accounts] == [
            ("acc_legacy", "raw_legacy", None),
            ("acc_workspace", "raw_workspace", "ws_new"),
        ]
