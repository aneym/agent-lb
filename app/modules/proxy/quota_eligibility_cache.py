from __future__ import annotations

import sys
import time
from collections.abc import Collection
from dataclasses import dataclass, replace

from app.modules.proxy import limit_backoff
from app.modules.proxy.account_cache import get_account_selection_cache

_CacheKey = tuple[str, str, str | None]
_GenerationKey = tuple[int, int]


@dataclass(frozen=True, slots=True)
class AnthropicQuotaEligibility:
    account_ids: list[str]
    preferred_account_ids: tuple[str, ...] = ()
    blocked_count: int = 0
    next_reset_at: int | None = None
    cooldown_blocked_count: int = 0
    cooldown_reset_at: int | None = None
    model_hold_count: int = 0
    weekly_exhausted_count: int = 0
    paid_fallback_account_ids: frozenset[str] = frozenset()
    burn_first_account_ids: frozenset[str] = frozenset()


def filter_attempted_eligibility(
    data: AnthropicQuotaEligibility,
    attempted_account_ids: Collection[str],
) -> AnthropicQuotaEligibility:
    if not attempted_account_ids or not data.preferred_account_ids:
        return data
    return replace(
        data,
        preferred_account_ids=tuple(
            account_id for account_id in data.preferred_account_ids if account_id not in attempted_account_ids
        ),
    )


@dataclass(slots=True)
class _CachedEligibility:
    data: AnthropicQuotaEligibility
    generation: _GenerationKey
    expires_at: float
    next_reset_at: int | None


class QuotaEligibilityCache:
    def __init__(self, ttl_seconds: float | None = None) -> None:
        if ttl_seconds is None:
            ttl_seconds = 0.0 if "pytest" in sys.modules else 5.0
        self._ttl_seconds = ttl_seconds
        self._cache: dict[_CacheKey, _CachedEligibility] = {}

    @property
    def generation(self) -> _GenerationKey:
        return (get_account_selection_cache().generation, limit_backoff.generation())

    def get(
        self,
        key: _CacheKey,
        *,
        now: int,
        attempted_account_ids: Collection[str] = (),
    ) -> AnthropicQuotaEligibility | None:
        if self._ttl_seconds <= 0.0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.generation != self.generation:
            self._cache.pop(key, None)
            return None
        if time.monotonic() >= entry.expires_at:
            self._cache.pop(key, None)
            return None
        if entry.next_reset_at is not None and now >= entry.next_reset_at:
            self._cache.pop(key, None)
            return None
        return filter_attempted_eligibility(entry.data, attempted_account_ids)

    def set(self, key: _CacheKey, data: AnthropicQuotaEligibility, *, generation: _GenerationKey) -> None:
        if self._ttl_seconds <= 0.0:
            return
        if generation != self.generation:
            return
        self._cache[key] = _CachedEligibility(
            data=data,
            generation=generation,
            expires_at=time.monotonic() + self._ttl_seconds,
            next_reset_at=data.next_reset_at,
        )

    def store_and_filter(
        self,
        key: _CacheKey,
        data: AnthropicQuotaEligibility,
        generation: _GenerationKey,
        attempted_account_ids: Collection[str],
    ) -> AnthropicQuotaEligibility:
        self.set(key, data, generation=generation)
        return filter_attempted_eligibility(data, attempted_account_ids)


_quota_eligibility_cache = QuotaEligibilityCache()


def get_quota_eligibility_cache() -> QuotaEligibilityCache:
    return _quota_eligibility_cache
