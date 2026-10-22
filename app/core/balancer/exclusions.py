from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TypeVar

from app.core.config.settings import get_settings

T = TypeVar("T")


def drop_excluded(accounts: Sequence[T], pool: Sequence[T], model: str | None) -> list[T]:
    now = time.time()
    rules = get_settings().model_account_exclusions.get(model or "", {})
    excluded = {account_id for account_id, until in rules.items() if until > now}
    if all(getattr(account, "id") in excluded for account in pool):
        return list(accounts)
    return [account for account in accounts if getattr(account, "id") not in excluded]
