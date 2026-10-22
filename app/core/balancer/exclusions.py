from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TypeVar

from app.core.config.settings import get_settings

T = TypeVar("T")


def drop_excluded(accounts: Sequence[T], model: str | None) -> list[T]:
    now = time.time()
    rules = get_settings().model_account_exclusions.get(model or "", {})
    excluded = {account_id for account_id, until in rules.items() if until > now}
    return [account for account in accounts if getattr(account, "id") not in excluded] or list(accounts)
