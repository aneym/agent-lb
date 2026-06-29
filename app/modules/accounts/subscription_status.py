from __future__ import annotations

from collections.abc import Iterable

from app.db.models import Account

ACTIVE_SUBSCRIPTION_STATUS = "active"
CANCELED_SUBSCRIPTION_STATUS = "canceled"


def normalize_subscription_status(status: str | None) -> str | None:
    normalized = (status or "").strip().lower()
    return normalized or None


def is_subscription_usable(account: Account) -> bool:
    return normalize_subscription_status(account.subscription_status) != CANCELED_SUBSCRIPTION_STATUS


def subscription_usable_accounts(accounts: Iterable[Account]) -> list[Account]:
    return [account for account in accounts if is_subscription_usable(account)]
