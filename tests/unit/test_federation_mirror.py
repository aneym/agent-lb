from __future__ import annotations

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository

pytestmark = pytest.mark.unit

_LOCAL_INSTANCE_ID = "laptop-mirror-unit-test"
_OWNER_INSTANCE_ID = "studio-mirror-unit-test"


async def _seed_locally_owned_account(account_id: str, *, owner_instance: str | None) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        provider="anthropic",
        chatgpt_account_id=None,
        email=f"{account_id}@example.com",
        alias="pre-existing-alias",
        plan_type="claude",
        access_token_encrypted=encryptor.encrypt("pre-existing-access"),
        refresh_token_encrypted=encryptor.encrypt("pre-existing-refresh"),
        id_token_encrypted=None,
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.owner_instance = owner_instance
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()


async def _get_account(account_id: str) -> Account:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        return account


@pytest.mark.asyncio
async def test_mirror_upsert_creates_new_row(db_setup: bool) -> None:
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        applied = await FederationRepository(session).upsert_mirror_account(
            account_id="acc_mirror_new",
            provider="anthropic",
            email="acc_mirror_new@example.com",
            alias="mirrored-alias",
            status="active",
            plan_type="claude",
            chatgpt_account_id=None,
            access_token="mirrored-access-1",
            owner_instance_id=_OWNER_INSTANCE_ID,
            local_instance_id=_LOCAL_INSTANCE_ID,
            encryptor=encryptor,
        )

    assert applied is True
    account = await _get_account("acc_mirror_new")
    assert account.owner_instance == _OWNER_INSTANCE_ID
    assert encryptor.decrypt(account.access_token_encrypted) == "mirrored-access-1"


@pytest.mark.asyncio
async def test_mirror_upsert_is_idempotent_and_updates_access_token(db_setup: bool) -> None:
    del db_setup
    encryptor = TokenEncryptor()

    async def _upsert(access_token: str) -> bool:
        async with SessionLocal() as session:
            return await FederationRepository(session).upsert_mirror_account(
                account_id="acc_mirror_repeat",
                provider="anthropic",
                email="acc_mirror_repeat@example.com",
                alias="mirrored-alias",
                status="active",
                plan_type="claude",
                chatgpt_account_id=None,
                access_token=access_token,
                owner_instance_id=_OWNER_INSTANCE_ID,
                local_instance_id=_LOCAL_INSTANCE_ID,
                encryptor=encryptor,
            )

    first_applied = await _upsert("mirrored-access-cycle-1")
    second_applied = await _upsert("mirrored-access-cycle-2")

    assert first_applied is True
    assert second_applied is True
    account = await _get_account("acc_mirror_repeat")
    assert account.owner_instance == _OWNER_INSTANCE_ID
    # Second cycle's fresher token wins; no duplicate row was created (a
    # duplicate primary key would have raised, not silently no-opped).
    assert encryptor.decrypt(account.access_token_encrypted) == "mirrored-access-cycle-2"


@pytest.mark.asyncio
async def test_mirror_upsert_does_not_clobber_locally_owned_row(db_setup: bool) -> None:
    del db_setup
    await _seed_locally_owned_account("acc_mirror_owned_null", owner_instance=None)
    await _seed_locally_owned_account("acc_mirror_owned_self", owner_instance=_LOCAL_INSTANCE_ID)
    encryptor = TokenEncryptor()

    async with SessionLocal() as session:
        repo = FederationRepository(session)
        applied_null = await repo.upsert_mirror_account(
            account_id="acc_mirror_owned_null",
            provider="anthropic",
            email="stale-mirror@example.com",
            alias="stale-alias",
            status="active",
            plan_type="claude",
            chatgpt_account_id=None,
            access_token="stale-mirror-access",
            owner_instance_id=_OWNER_INSTANCE_ID,
            local_instance_id=_LOCAL_INSTANCE_ID,
            encryptor=encryptor,
        )
        applied_self = await repo.upsert_mirror_account(
            account_id="acc_mirror_owned_self",
            provider="anthropic",
            email="stale-mirror@example.com",
            alias="stale-alias",
            status="active",
            plan_type="claude",
            chatgpt_account_id=None,
            access_token="stale-mirror-access",
            owner_instance_id=_OWNER_INSTANCE_ID,
            local_instance_id=_LOCAL_INSTANCE_ID,
            encryptor=encryptor,
        )

    assert applied_null is False
    assert applied_self is False

    account_null = await _get_account("acc_mirror_owned_null")
    account_self = await _get_account("acc_mirror_owned_self")
    assert account_null.owner_instance is None
    assert account_self.owner_instance == _LOCAL_INSTANCE_ID
    assert encryptor.decrypt(account_null.access_token_encrypted) == "pre-existing-access"
    assert encryptor.decrypt(account_self.access_token_encrypted) == "pre-existing-access"
