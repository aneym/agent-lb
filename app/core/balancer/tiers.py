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


def shared_first(states: Iterable[T], *, allow_personal_fallback: bool = True) -> list[T]:
    pool = list(states)
    prefixes = personal_prefixes()
    if not prefixes:
        return pool
    shared = [state for state in pool if not state.account_id.startswith(prefixes)]
    if shared or not allow_personal_fallback:
        return shared
    return pool
