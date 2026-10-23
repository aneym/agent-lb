from __future__ import annotations

from sqlalchemy import ColumnElement, func, or_, select, true

from app.db.models import Account
from app.modules.accounts.subscription_status import CANCELED_SUBSCRIPTION_STATUS


def visible_account_clause(account_id: ColumnElement, enabled: bool) -> ColumnElement[bool]:
    if not enabled:
        return true()
    canceled = select(Account.id).where(
        func.lower(func.trim(Account.subscription_status)) == CANCELED_SUBSCRIPTION_STATUS
    )
    return or_(account_id.is_(None), account_id.not_in(canceled))
