from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.account_state_models import AccountExchangeIntent
from app.db.models import Account, AccountPlanDowngradeObservation, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def account(account_id: str, *, provider: str = "openai", workspace: str | None = None) -> Account:
    return Account(
        id=account_id,
        provider=provider,
        email="shared@example.invalid",
        chatgpt_account_id="shared-identity",
        chatgpt_user_id="seat-1",
        workspace_id=workspace,
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


async def test_upserts_keep_provider_credentials_separate(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        original = await repo.upsert(account("shared", provider="anthropic"))
        imported = await repo.upsert(account("shared"), merge_by_chatgpt_identity=True)
        assert imported.id != original.id
        assert original.provider == "anthropic"
        assert imported.provider == "openai"
        again = await repo.upsert_account_slot(
            account("new-slot", workspace="workspace-1"),
            preserve_unknown_workspace_duplicates=False,
            preserve_identity_slots=True,
        )
        assert again.provider == "openai"
        assert original.workspace_id is None
        assert await repo.replace_reauthorized(original.id, account("new")) is None
        assert len((await session.execute(select(Account))).scalars().all()) == 2


async def test_slot_replacement_clears_old_custody_and_downgrade_evidence(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        original = await repo.upsert_account_slot(account("slot", workspace="workspace-1"))
        original.owner_instance = "remote-owner"
        original.codex_installation_id = "installation-1"
        session.add(
            AccountExchangeIntent(
                account_id=original.id,
                refresh_token_sha256="0" * 64,
                started_at=utcnow(),
                replay=True,
            )
        )
        session.add(
            AccountPlanDowngradeObservation(
                account_id=original.id,
                observations=1,
                credential_fingerprint="1" * 64,
                observed_plan_type="free",
                first_observed_at=utcnow(),
                last_observed_at=utcnow(),
            )
        )
        await session.commit()
        replacement = account("incoming", workspace="workspace-1")
        replacement.refresh_token_encrypted = b"new-refresh"
        replacement.access_expires_at = utcnow() + timedelta(hours=1)
        saved = await repo.upsert_account_slot(
            replacement, preserve_unknown_workspace_duplicates=False, preserve_identity_slots=True
        )
        assert saved.id == original.id
        assert saved.refresh_token_encrypted == b"new-refresh"
        assert saved.access_expires_at == replacement.access_expires_at
        assert saved.owner_instance == "remote-owner"
        assert saved.codex_installation_id == "installation-1"
        assert await session.get(AccountExchangeIntent, saved.id) is None
        assert await session.get(AccountPlanDowngradeObservation, saved.id) is None


async def test_identity_upsert_reconciles_matching_seat_only(db_setup):
    async with SessionLocal() as session:
        session.add_all([account("first"), account("duplicate")])
        other = account("other-seat")
        other.email = "other@example.invalid"
        session.add(other)
        await session.commit()
        saved = await AccountsRepository(session).upsert(
            account("incoming"), merge_by_email=False, merge_by_chatgpt_identity=True
        )
        assert saved.id in {"first", "duplicate"}
        assert await session.get(Account, "other-seat") is not None
        assert len((await session.execute(select(Account))).scalars().all()) == 2
