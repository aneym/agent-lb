from __future__ import annotations

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus


def _make_account(
    account_id: str,
    email: str,
    plan_type: str = "plus",
    status: AccountStatus = AccountStatus.ACTIVE,
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
    )
