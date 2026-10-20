from __future__ import annotations

from collections.abc import Callable

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.migrate import (
    inspect_migration_state,
)
from app.db.models import Account, AccountStatus

try:
    from app.db.migrate import check_migration_policy as _check_migration_policy
except ImportError:
    check_migration_policy: Callable[[str], tuple[str, ...]] | None = None
else:
    check_migration_policy = _check_migration_policy

_DATABASE_URL = get_settings().database_url
_HEAD_REVISION = inspect_migration_state(_DATABASE_URL).head_revision


def _is_postgresql_database_url(url: str) -> bool:
    return url.startswith("postgresql+")


def _make_account(account_id: str, email: str, plan_type: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
