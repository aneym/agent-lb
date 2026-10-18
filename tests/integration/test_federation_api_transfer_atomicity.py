from __future__ import annotations
import pytest
from sqlalchemy import select
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, AccountTransferDirection, FederationUsageDaily, RequestLog
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository
from tests.integration.federation_api_support import (
    _LOCAL_INSTANCE_ID,
    _TAKER_INSTANCE_ID,
    _get_account,
    _seed_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_postgres_checkout_crash_before_and_after_commit_retries_without_double_ownership(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_setup
    from app.core.config.settings import Settings
    from app.db.models import AccountTransfer, AccountTransferDirection
    from app.modules.federation.exceptions import FederationConflictError
    from app.modules.federation.service import FederationService

    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires disposable Postgres to verify durable transaction boundaries")
    await _seed_account("checkout-crash", owner_instance=None, refresh_token="live-refresh")
    settings = Settings(local_instance_id=_LOCAL_INSTANCE_ID)
    encryptor = TokenEncryptor()
    retry_nonce = ""
    for crash_before_commit in (True, False):
        async with SessionLocal() as session:
            repo = FederationRepository(session)
            original_commit = session.commit

            async def crash_commit() -> None:
                if crash_before_commit:
                    await session.flush()
                    raise RuntimeError("process interrupted before commit")
                await original_commit()
                raise RuntimeError("response lost after commit")

            monkeypatch.setattr(session, "commit", crash_commit)
            with pytest.raises(RuntimeError, match="interrupted|response lost"):
                await FederationService(repo, settings=settings, encryptor=encryptor).checkout(
                    "checkout-crash", _TAKER_INSTANCE_ID, "crash-nonce-12345678901234567890"
                )
        async with SessionLocal() as session:
            repo = FederationRepository(session)
            if crash_before_commit:
                assert (await repo.get_account("checkout-crash")).owner_instance is None
            if crash_before_commit:
                retry = await FederationService(repo, settings=settings, encryptor=encryptor).checkout(
                    "checkout-crash", _TAKER_INSTANCE_ID, "crash-retry-nonce-12345678901234567890"
                )
                assert retry.auth.refresh_token == "live-refresh"
            else:
                with pytest.raises(FederationConflictError):
                    await FederationService(repo, settings=settings, encryptor=encryptor).checkout(
                        "checkout-crash", _TAKER_INSTANCE_ID, "crash-nonce-12345678901234567890"
                    )
                retry_nonce = "crash-nonce-12345678901234567890"
            rows = (
                (
                    await session.execute(
                        select(AccountTransfer).where(
                            AccountTransfer.account_id == "checkout-crash",
                            AccountTransfer.direction == AccountTransferDirection.CHECKOUT,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].nonce == (retry.nonce if crash_before_commit else retry_nonce)
            assert (await repo.get_account("checkout-crash")).owner_instance == _TAKER_INSTANCE_ID
        if crash_before_commit:
            async with SessionLocal() as session:
                from sqlalchemy import delete

                await session.execute(delete(AccountTransfer).where(AccountTransfer.account_id == "checkout-crash"))
                (await session.get(Account, "checkout-crash")).owner_instance = None
                await session.commit()

    async with SessionLocal() as session:
        service = FederationService(FederationRepository(session), settings=settings, encryptor=encryptor)
        await service.confirm_checkout(retry_nonce)
    assert encryptor.decrypt((await _get_account("checkout-crash")).refresh_token_encrypted) == ""


@pytest.mark.asyncio
async def test_postgres_checkin_crash_before_commit_preserves_owner_and_old_token(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_setup
    from app.core.config.settings import Settings
    from app.db.models import AccountTransfer, AccountTransferDirection
    from app.modules.federation.schemas import FederationAuthPayload
    from app.modules.federation.service import FederationService

    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires disposable Postgres")
    await _seed_account("checkin-crash", owner_instance=_TAKER_INSTANCE_ID)
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.create_transfer(
            account_id="checkin-crash",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id=_TAKER_INSTANCE_ID,
            nonce="prior-checkout-crash",
        )
        await repo.mark_transfer_settled("prior-checkout-crash")
    payload = FederationAuthPayload(
        access_token="new-access",
        refresh_token="new-refresh",
        id_token=None,
        expires_at_ms=None,
        provider="anthropic",
        email="checkin-crash@example.com",
        alias=None,
        status="active",
        plan_type="claude",
        chatgpt_account_id=None,
    )
    async with SessionLocal() as session:

        async def crash_commit() -> None:
            await session.flush()
            raise RuntimeError("process interrupted before commit")

        monkeypatch.setattr(session, "commit", crash_commit)
        with pytest.raises(RuntimeError, match="interrupted"):
            await FederationService(
                FederationRepository(session),
                settings=Settings(
                    local_instance_id=_LOCAL_INSTANCE_ID, federation_taker_instance_ids=[_TAKER_INSTANCE_ID]
                ),
            ).checkin("checkin-crash", "return-nonce-12345678901234567890", _TAKER_INSTANCE_ID, payload)
    account = await _get_account("checkin-crash")
    assert account.owner_instance == _TAKER_INSTANCE_ID
    assert TokenEncryptor().decrypt(account.refresh_token_encrypted) == "seed-refresh"

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AccountTransfer).where(
                        AccountTransfer.account_id == "checkin-crash",
                        AccountTransfer.direction == AccountTransferDirection.CHECKIN,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
        await FederationService(
            FederationRepository(session),
            settings=Settings(local_instance_id=_LOCAL_INSTANCE_ID, federation_taker_instance_ids=[_TAKER_INSTANCE_ID]),
        ).checkin("checkin-crash", "return-nonce-12345678901234567890", _TAKER_INSTANCE_ID, payload)
    account = await _get_account("checkin-crash")
    assert account.owner_instance is None
    assert TokenEncryptor().decrypt(account.refresh_token_encrypted) == "new-refresh"


@pytest.mark.asyncio
async def test_postgres_checkin_has_no_committed_import_before_owner_change(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch
) -> None:

    from app.core.config.settings import Settings
    from app.db.models import AccountTransferDirection
    from app.modules.federation.schemas import FederationAuthPayload
    from app.modules.federation.service import FederationService

    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires disposable Postgres")
    await _seed_account("checkin-one-commit", owner_instance=_TAKER_INSTANCE_ID)
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.create_transfer(
            account_id="checkin-one-commit",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id=_TAKER_INSTANCE_ID,
            nonce="prior-checkout-one-commit",
        )
        await repo.mark_transfer_settled("prior-checkout-one-commit")
    payload = FederationAuthPayload(
        access_token="new-access",
        refresh_token="new-refresh",
        id_token=None,
        expires_at_ms=None,
        provider="anthropic",
        email="checkin-one-commit@example.com",
        alias=None,
        status="active",
        plan_type="claude",
        chatgpt_account_id=None,
    )
    async with SessionLocal() as session:
        original_commit = session.commit
        commits = 0

        async def fail_on_second_commit() -> None:
            nonlocal commits
            commits += 1
            if commits == 2:
                await session.flush()
                raise RuntimeError("interrupted between import and owner change")
            await original_commit()

        monkeypatch.setattr(session, "commit", fail_on_second_commit)
        try:
            await FederationService(
                FederationRepository(session),
                settings=Settings(
                    local_instance_id=_LOCAL_INSTANCE_ID, federation_taker_instance_ids=[_TAKER_INSTANCE_ID]
                ),
            ).checkin("checkin-one-commit", "single-nonce-12345678901234567890", _TAKER_INSTANCE_ID, payload)
        except RuntimeError:
            pass
    account = await _get_account("checkin-one-commit")
    assert not (
        account.owner_instance == _TAKER_INSTANCE_ID
        and TokenEncryptor().decrypt(account.refresh_token_encrypted) == "new-refresh"
    )
    assert account.owner_instance is None
    assert TokenEncryptor().decrypt(account.refresh_token_encrypted) == "new-refresh"
