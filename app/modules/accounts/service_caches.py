from __future__ import annotations

from app.modules.accounts.read_cache import StaleWhileRevalidate
from app.modules.accounts.schemas import AccountAdditionalQuota, AccountRequestUsage

USAGE_CACHE_TTL = 60.0


usage_cache: StaleWhileRevalidate[dict[str, AccountRequestUsage]] = StaleWhileRevalidate(
    "request_usage", ttl_seconds=USAGE_CACHE_TTL
)


QUOTA_CACHE_TTL = 12.0


quota_cache: StaleWhileRevalidate[dict[str, list[AccountAdditionalQuota]]] = StaleWhileRevalidate(
    "additional_quotas", ttl_seconds=QUOTA_CACHE_TTL
)


def clear_account_caches() -> None:

    usage_cache.clear()
    quota_cache.clear()


def invalidate_additional_quotas_cache() -> None:

    quota_cache.clear()


async def cancel_account_cache_refreshes() -> None:

    await usage_cache.cancel_refresh()
    await quota_cache.cancel_refresh()
