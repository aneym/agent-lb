from __future__ import annotations

from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.federation_models import AccountTransfer, AccountTransferState
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.federation.schemas import FederationAuthPayload


def _auth() -> FederationAuthPayload:
    return FederationAuthPayload(
        access_token="new-access",
        refresh_token="new-refresh",
        provider="anthropic",
        email="custody@example.invalid",
        status="active",
        plan_type="pro",
    )


async def _account(account_id: str, owner: str | None) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                provider="anthropic",
                email="custody@example.invalid",
                alias=None,
                plan_type="pro",
                access_token_encrypted=encryptor.encrypt("old-access"),
                refresh_token_encrypted=encryptor.encrypt("old-refresh"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                owner_instance=owner,
            )
        )
        await session.commit()


async def _state(account_id: str) -> tuple[str | None, str, list[AccountTransferState]]:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        rows = (
            (await session.execute(select(AccountTransfer).where(AccountTransfer.account_id == account_id)))
            .scalars()
            .all()
        )
        return (
            account.owner_instance,  # type: ignore
            TokenEncryptor().decrypt(account.refresh_token_encrypted),  # type: ignore
            [row.state for row in rows],
        )
