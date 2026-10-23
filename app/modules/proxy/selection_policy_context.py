"""Request-local provider policy carried through the shared selection helpers."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from app.core.providers import OPENAI_PROVIDER_NAME


@dataclass(frozen=True, slots=True)
class ProviderSelectionPolicy:
    provider: str = OPENAI_PROVIDER_NAME
    ignore_primary_quota_account_ids: frozenset[str] = frozenset()
    burn_first_account_ids: frozenset[str] = frozenset()
    burn_first_sticky_drain: bool = False
    headroom_reallocate: bool = False
    hold_sticky_until_exhausted: bool = False


provider_selection_policy: ContextVar[ProviderSelectionPolicy] = ContextVar(
    "provider_selection_policy", default=ProviderSelectionPolicy()
)
