from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypeVar

from app.core.config.settings import get_settings


class _HasAccountId(Protocol):
    account_id: str


T = TypeVar("T", bound=_HasAccountId)


def personal_prefixes() -> tuple[str, ...]:
    raw = get_settings().pool_personal_account_ids
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def account_tier(account_id: str) -> str:
    return "personal" if account_id.startswith(personal_prefixes()) else "shared"


def shared_first(states: Iterable[T]) -> list[T]:
    pool = list(states)
    prefixes = personal_prefixes()
    shared = [state for state in pool if not state.account_id.startswith(prefixes)] if prefixes else pool
    return shared or pool
