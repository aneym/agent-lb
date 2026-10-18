from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountLimitWarmup,
    AccountStatus,
    AdditionalUsageHistory,
    ApiKey,
    ApiKeyAccountAssignment,
    HttpBridgeSessionRecord,
    RequestLog,
    StickySession,
    StickySessionKind,
    UsageHistory,
)
from app.db.session import SessionLocal
from app.modules.accounts.repository import (
    AccountsRepository,
)
from tests.integration.repository_account_support import _make_account

pytestmark = pytest.mark.integration


def _make_account_with_chatgpt_id(account_id: str, email: str, chatgpt_id: str) -> Account:
    account = _make_account(account_id, email)
    account.chatgpt_account_id = chatgpt_id
    return account


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_reuses_deactivated_row(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        original = _make_account_with_chatgpt_id("acc_canonical", "reauth@example.com", "chatgpt_xyz")
        await repo.upsert(original, merge_by_email=False)

        await repo.update_status(
            "acc_canonical",
            AccountStatus.DEACTIVATED,
            "Refresh token was revoked - re-login required",
        )

        reauth = _make_account_with_chatgpt_id("acc_canonical", "reauth@example.com", "chatgpt_xyz")
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_canonical"
        assert saved.plan_type == "team"
        assert saved.status == AccountStatus.ACTIVE
        assert saved.deactivation_reason is None

        rows = list(
            (await session.execute(select(Account).where(Account.chatgpt_account_id == "chatgpt_xyz"))).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].id == "acc_canonical"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_picks_oldest_canonical(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        canonical = _make_account_with_chatgpt_id("acc_first", "shared@example.com", "chatgpt_dup")
        canonical.created_at = utcnow() - timedelta(days=30)
        await repo.upsert(canonical, merge_by_email=False)

        copy_row = _make_account_with_chatgpt_id("acc_first__copy2", "shared@example.com", "chatgpt_dup")
        copy_row.created_at = utcnow()
        await repo.upsert(copy_row, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_first", "shared@example.com", "chatgpt_dup")
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_first"
        assert saved.plan_type == "team"
        rows = list(
            (await session.execute(select(Account).where(Account.chatgpt_account_id == "chatgpt_dup"))).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].id == "acc_first"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_does_not_clear_workspace_on_workspace_less_reauth(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        workspace = _make_account_with_chatgpt_id("acc_workspace", "shared@example.com", "chatgpt_workspace_less")
        workspace.workspace_id = "ws_business"
        workspace.workspace_label = "Business"
        await repo.upsert(workspace, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_reauth", "shared@example.com", "chatgpt_workspace_less")
        reauth.workspace_id = None
        reauth.workspace_label = None
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_reauth"
        assert saved.workspace_id is None

        stored_workspace = (await session.execute(select(Account).where(Account.id == "acc_workspace"))).scalar_one()
        assert stored_workspace.workspace_id == "ws_business"
        assert stored_workspace.workspace_label == "Business"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_preserves_workspace_when_deterministic_id_matches(
    db_setup,
):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        workspace = _make_account_with_chatgpt_id("acc_reauth", "same-id-workspace@example.com", "chatgpt_same_id")
        workspace.workspace_id = "ws_learned"
        workspace.workspace_label = "Learned Workspace"
        workspace.seat_type = "member"
        workspace.plan_type = "team"
        await repo.upsert(workspace, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_reauth", "same-id-workspace@example.com", "chatgpt_same_id")
        reauth.workspace_id = None
        reauth.workspace_label = None
        reauth.seat_type = None
        reauth.plan_type = "plus"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_reauth"
        assert saved.workspace_id == "ws_learned"
        assert saved.workspace_label == "Learned Workspace"
        assert saved.seat_type == "member"
        assert saved.plan_type == "plus"

        accounts = list((await session.execute(select(Account))).scalars().all())
        assert [(account.id, account.workspace_id) for account in accounts] == [("acc_reauth", "ws_learned")]


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_workspace_less_reauth_uses_unknown_row(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        unknown = _make_account_with_chatgpt_id("acc_unknown", "shared@example.com", "chatgpt_unknown_reauth")
        unknown.created_at = utcnow() - timedelta(days=30)
        await repo.upsert(unknown, merge_by_email=False)

        workspace = _make_account_with_chatgpt_id("acc_workspace", "shared@example.com", "chatgpt_unknown_reauth")
        workspace.workspace_id = "ws_business"
        workspace.created_at = utcnow() - timedelta(days=10)
        await repo.upsert(workspace, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_reauth", "shared@example.com", "chatgpt_unknown_reauth")
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_unknown"
        assert saved.plan_type == "team"
        assert saved.workspace_id is None

        stored_workspace = (await session.execute(select(Account).where(Account.id == "acc_workspace"))).scalar_one()
        assert stored_workspace.workspace_id == "ws_business"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_prefers_matching_workspace_row(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        unknown = _make_account_with_chatgpt_id("acc_unknown", "shared@example.com", "chatgpt_workspace_reauth")
        unknown.created_at = utcnow() - timedelta(days=30)
        await repo.upsert(unknown, merge_by_email=False)

        workspace = _make_account_with_chatgpt_id("acc_workspace", "shared@example.com", "chatgpt_workspace_reauth")
        workspace.workspace_id = "ws_business"
        workspace.created_at = utcnow() - timedelta(days=10)
        await repo.upsert(workspace, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_reauth", "shared@example.com", "chatgpt_workspace_reauth")
        reauth.workspace_id = "ws_business"
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_workspace"
        assert saved.plan_type == "team"
        assert saved.workspace_id == "ws_business"

        stored_unknown = (await session.execute(select(Account).where(Account.id == "acc_unknown"))).scalar_one()
        assert stored_unknown.workspace_id is None


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_reconciles_duplicate_rows(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        canonical = _make_account_with_chatgpt_id("acc_merge_main", "merge@example.com", "chatgpt_merge")
        await repo.upsert(canonical, merge_by_email=False)

        duplicate = _make_account_with_chatgpt_id("acc_merge_main__copy2", "merge@example.com", "chatgpt_merge")
        await repo.upsert(duplicate, merge_by_email=False)
        duplicate_again = _make_account_with_chatgpt_id("acc_merge_main__copy3", "merge@example.com", "chatgpt_merge")
        await repo.upsert(duplicate_again, merge_by_email=False)

        duplicate_row = (
            await session.execute(select(Account).where(Account.id == "acc_merge_main__copy2"))
        ).scalar_one()
        duplicate_again_row = (
            await session.execute(select(Account).where(Account.id == "acc_merge_main__copy3"))
        ).scalar_one()

        api_key = ApiKey(
            id="api_merge_dupe",
            name="Merge Test",
            key_hash="merge-key-hash",
            key_prefix="mrg",
            apply_to_codex_model=False,
            account_assignment_scope_enabled=False,
        )
        session.add(api_key)
        session.add(ApiKeyAccountAssignment(api_key_id=api_key.id, account_id=duplicate_row.id))
        session.add(ApiKeyAccountAssignment(api_key_id=api_key.id, account_id=duplicate_again_row.id))
        session.add(
            UsageHistory(
                account_id=duplicate_row.id,
                window="primary",
                used_percent=55.0,
                input_tokens=42,
                output_tokens=17,
                reset_at=1,
                window_minutes=300,
            )
        )
        session.add(
            AdditionalUsageHistory(
                account_id=duplicate_row.id,
                quota_key="gpt-5.1",
                limit_name="gpt-5.1",
                metered_feature="model",
                window="7d",
                used_percent=15.0,
            )
        )
        session.add(
            AccountLimitWarmup(
                account_id=canonical.id,
                window="primary",
                reset_at=123,
                status="completed",
                model="gpt-5.1",
                attempted_at=utcnow() - timedelta(minutes=5),
            )
        )
        session.add(
            AccountLimitWarmup(
                account_id=duplicate_row.id,
                window="primary",
                reset_at=123,
                status="pending",
                model="gpt-5.1",
                attempted_at=utcnow(),
            )
        )
        session.add(
            StickySession(
                account_id=duplicate_row.id,
                key="sticky-dup",
                kind=StickySessionKind.STICKY_THREAD,
            )
        )
        session.add(
            HttpBridgeSessionRecord(
                account_id=duplicate_row.id,
                session_key_kind="http",
                session_key_value="turn:merge",
                session_key_hash="turn-merge-hash",
                api_key_scope="merge-scope",
            )
        )
        session.add(
            RequestLog(
                request_id="req_merge_dupe",
                account_id=duplicate_row.id,
                model="gpt-5",
                status="success",
                requested_at=utcnow(),
            )
        )
        await session.commit()

        reauth = _make_account_with_chatgpt_id("acc_merge_main", "merge@example.com", "chatgpt_merge")
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_merge_main"
        assert saved.plan_type == "team"

        rows = list(
            (await session.execute(select(Account).where(Account.chatgpt_account_id == "chatgpt_merge")))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == "acc_merge_main"

        account_histories = (
            (await session.execute(select(UsageHistory).where(UsageHistory.account_id == "acc_merge_main")))
            .scalars()
            .all()
        )
        assert len(account_histories) == 1
        assert account_histories[0].used_percent == 55.0

        additional_histories = (
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "acc_merge_main")
                )
            )
            .scalars()
            .all()
        )
        assert len(additional_histories) == 1
        assert additional_histories[0].quota_key == "gpt-5.1"

        account_warmups = (
            (await session.execute(select(AccountLimitWarmup).where(AccountLimitWarmup.account_id == "acc_merge_main")))
            .scalars()
            .all()
        )
        assert len(account_warmups) == 1
        assert account_warmups[0].status == "completed"

        sticky_sessions = (
            (await session.execute(select(StickySession).where(StickySession.account_id == "acc_merge_main")))
            .scalars()
            .all()
        )
        assert len(sticky_sessions) == 1
        assert sticky_sessions[0].key == "sticky-dup"

        bridge_sessions = (
            (
                await session.execute(
                    select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.account_id == "acc_merge_main")
                )
            )
            .scalars()
            .all()
        )
        assert len(bridge_sessions) == 1
        assert bridge_sessions[0].session_key_value == "turn:merge"

        assignments = (
            (
                await session.execute(
                    select(ApiKeyAccountAssignment).where(ApiKeyAccountAssignment.account_id == "acc_merge_main")
                )
            )
            .scalars()
            .all()
        )
        assert len(assignments) == 1
        assert assignments[0].api_key_id == api_key.id

        duplicate_request_logs = (
            (await session.execute(select(RequestLog).where(RequestLog.account_id == "acc_merge_main__copy2")))
            .scalars()
            .all()
        )
        assert len(duplicate_request_logs) == 0


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_creates_when_missing(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        incoming = _make_account_with_chatgpt_id("acc_new", "new@example.com", "chatgpt_fresh")
        saved = await repo.upsert(incoming, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_new"
        assert saved.chatgpt_account_id == "chatgpt_fresh"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_skips_without_upstream_id(db_setup):

    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        first = _make_account("acc_no_id", "noid@example.com")
        await repo.upsert(first, merge_by_email=False, merge_by_chatgpt_identity=True)

        again = _make_account("acc_no_id", "noid@example.com")
        again.plan_type = "team"
        saved = await repo.upsert(again, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id.startswith("acc_no_id__copy")
