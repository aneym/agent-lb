from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TypeVar

from app.core.config.settings import get_settings

T = TypeVar("T")


def active_model_exclusions(model: str | None, now: float) -> dict[str, float]:
    rules = get_settings().model_account_exclusions.get(model or "", {})
    return {account_id: until for account_id, until in rules.items() if until > now}


def drop_excluded(accounts: Sequence[T], pool: Sequence[T], model: str | None) -> list[T]:
    excluded = active_model_exclusions(model, time.time())
    if all(getattr(account, "id") in excluded for account in pool):
        return list(accounts)
    return [account for account in accounts if getattr(account, "id") not in excluded]
