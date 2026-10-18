from __future__ import annotations

from datetime import timedelta

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal, get_background_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository


async def _account(account_id: str) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                provider="anthropic",
                email=f"{account_id}@example.invalid",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                last_refresh=utcnow(),
                access_expires_at=utcnow() + timedelta(hours=1),
                status=AccountStatus.ACTIVE,
            )
        )


async def _refresh(account_id: str) -> Account:
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        return await AuthManager(repo).refresh_account(account)


async def _refresh_in_background_session(account_id: str) -> Account:

    async with get_background_session() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        return await AuthManager(repo).refresh_account(account)


async def _direct_route(*args, **kwargs):
    return None
