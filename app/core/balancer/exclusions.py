from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from app.core.config.settings import get_settings

T = TypeVar("T")


def drop_excluded(accounts: Sequence[T], model: str | None) -> list[T]:
    excluded = set(get_settings().model_account_exclusions.get(model or "", ()))
    return [account for account in accounts if getattr(account, "id") not in excluded] or list(accounts)
