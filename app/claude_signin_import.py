from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.claude_signin import ClaudeSignIn
from app.core.anthropic.oauth import ANTHROPIC_PROVIDER_NAME


@dataclass(frozen=True)
class ImportedPlan:
    account: str
    plan_type: str
    status: str
    access_expires_at: datetime | None
    created: bool


async def store(sign_in: ClaudeSignIn) -> ImportedPlan:
    from app.core.crypto import TokenEncryptor
    from app.db.models import Account
    from app.db.session import SessionLocal, close_db, init_db
    from app.modules.accounts.repository import AccountsRepository
    from app.modules.oauth.token_account import account_from_tokens

    await init_db()
    try:
        account = account_from_tokens(sign_in.tokens, ANTHROPIC_PROVIDER_NAME, TokenEncryptor())
        async with SessionLocal() as session:
            created = await session.get(Account, account.id) is None
            saved = await AccountsRepository(session).upsert(
                account, merge_by_email=False, merge_by_chatgpt_identity=True
            )
            status = saved.status.value if hasattr(saved.status, "value") else str(saved.status)
            return ImportedPlan(
                account=saved.id[:8],
                plan_type=saved.plan_type,
                status=status,
                access_expires_at=saved.access_expires_at,
                created=created,
            )
    finally:
        await close_db()
