from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TypeVar, cast

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    normalize_provider_name,
)
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import AccountStatus, AdditionalUsageHistory
from app.db.session import get_background_session
from app.modules.accounts import probes
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.credits import CREDITS_USAGE_WINDOW
from app.modules.accounts.mappers import build_account_summaries, build_account_usage_trends
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.reset_credit_attempts import ResetCreditAttemptsRepository
from app.modules.accounts.schemas import (
    AccountAdditionalQuota,
    AccountAdditionalWindow,
    AccountRequestUsage,
    AccountSummary,
    AccountTrendsResponse,
    AnthropicModelsResponse,
)
from app.modules.accounts.service_caches import quota_cache, usage_cache
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.usage.additional_quota_keys import (
    get_additional_display_label_for_quota_key,
    get_additional_quota_routing_policy,
    list_additional_quota_definitions,
)
from app.modules.usage.refresh_ports import AdditionalUsageRepositoryPort
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater

_SPARKLINE_DAYS = 7


_DETAIL_BUCKET_SECONDS = 3600


ANTHROPIC_MODELS_TTL_SECONDS = 3600.0


ANTHROPIC_MODELS_MAX_ACCOUNTS = 3


_LIMITED_LISTABLE_STATUSES = (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)


_anthropic_models: tuple[float, datetime, list[str]] | None = None


_anthropic_models_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _anthropic_models_refresh_lock() -> asyncio.Lock:
    return _anthropic_models_locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())


_T = TypeVar("_T")


_S = TypeVar("_S")


_ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"


_ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW = "primary"


async def with_background_service(service_type: Callable[..., _S], load: Callable[[_S], Awaitable[_T]]) -> _T:

    async with get_background_session() as session:
        service = service_type(
            AccountsRepository(session),
            UsageRepository(session),
            AdditionalUsageRepository(session),
            LimitWarmupRepository(session),
        )
        return await load(service)


def _additional_quota_window(
    entry: AdditionalUsageHistory | None,
    *,
    now_epoch: int,
) -> AccountAdditionalWindow | None:
    if entry is None:
        return None

    if float(entry.used_percent) >= 100.0 and (entry.reset_at is None or entry.reset_at <= now_epoch):
        return AccountAdditionalWindow(
            used_percent=0.0,
            reset_at=None,
            window_minutes=None,
        )
    return AccountAdditionalWindow(
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
    )


class AccountsQueries:
    def __init__(
        self,
        repo: AccountsRepository,
        usage_repo: UsageRepository | None = None,
        additional_usage_repo: AdditionalUsageRepository | AdditionalUsageRepositoryPort | None = None,
        limit_warmup_repo: LimitWarmupRepository | None = None,
        auth_manager: AuthManager | None = None,
    ) -> None:
        self._repo = repo
        self._usage_repo = usage_repo
        self._additional_usage_repo = additional_usage_repo
        self._limit_warmup_repo = limit_warmup_repo
        self._usage_updater = UsageUpdater(usage_repo, repo, additional_usage_repo) if usage_repo else None
        self._encryptor = TokenEncryptor()
        self._auth_manager = auth_manager
        self._reset_attempts: ResetCreditAttemptsRepository | None = None

    async def list_accounts(
        self,
        *,
        include_request_usage: bool = False,
        owner_source_names: dict[str, str] | None = None,
        account_ids: list[str] | None = None,
    ) -> list[AccountSummary]:
        accounts = (
            await self._repo.list_accounts_by_ids(account_ids)
            if account_ids is not None
            else await self._repo.list_accounts()
        )
        if not accounts:
            return []
        account_ids = [account.id for account in accounts]
        account_id_set = set(account_ids)
        primary_usage = await self._usage_repo.latest_by_account(window="primary") if self._usage_repo else {}
        secondary_usage = await self._usage_repo.latest_by_account(window="secondary") if self._usage_repo else {}
        monthly_usage = await self._usage_repo.latest_by_account(window="monthly") if self._usage_repo else {}
        credits_usage = (
            await self._usage_repo.latest_by_account(window=CREDITS_USAGE_WINDOW) if self._usage_repo else {}
        )
        limit_warmups_by_account = (
            await self._limit_warmup_repo.latest_by_account(account_ids) if self._limit_warmup_repo else {}
        )
        last_primed_by_account = (
            await self._limit_warmup_repo.last_primed_by_account(account_ids) if self._limit_warmup_repo else {}
        )

        request_usage_by_account: dict[str, AccountRequestUsage] = {}
        if include_request_usage:
            request_usage_by_account = await self._request_usage_by_account(account_ids)
        additional_quotas_by_account = await self._additional_quotas_by_account(account_ids, account_id_set)
        fable_scoped_weekly_by_account = await self._fable_scoped_weekly_by_account(account_ids)

        return build_account_summaries(
            accounts=accounts,
            primary_usage=primary_usage,
            secondary_usage=secondary_usage,
            monthly_usage=monthly_usage,
            credits_usage=credits_usage,
            request_usage_by_account=request_usage_by_account,
            additional_quotas_by_account=additional_quotas_by_account,
            limit_warmups_by_account=limit_warmups_by_account,
            last_primed_by_account=last_primed_by_account,
            fable_scoped_weekly_by_account=fable_scoped_weekly_by_account,
            owner_source_names=owner_source_names,
            encryptor=self._encryptor,
        )

    async def _fable_scoped_weekly_by_account(self, account_ids: list[str]) -> dict[str, AdditionalUsageHistory]:

        if not self._additional_usage_repo:
            return {}
        additional_usage_repo = cast(AdditionalUsageRepository, self._additional_usage_repo)
        return await additional_usage_repo.latest_by_account(
            _ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY,
            _ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW,
            account_ids=account_ids,
        )

    async def warm_read_caches(self, *, include_request_usage: bool) -> None:

        account_ids = [account.id for account in await self._repo.list_accounts()]
        if not account_ids:
            return
        key = frozenset(account_ids)
        account_id_set = set(account_ids)
        await quota_cache.refresh_now(key, lambda: self._load_additional_quotas(account_ids, account_id_set))
        if include_request_usage:
            await usage_cache.refresh_now(key, lambda: self._load_request_usage(account_ids))

    async def _request_usage_by_account(self, account_ids: list[str]) -> dict[str, AccountRequestUsage]:

        return await usage_cache.get(
            frozenset(account_ids),
            load=lambda: self._load_request_usage(account_ids),
            refresh=lambda: with_background_service(
                type(self), lambda service: service._load_request_usage(account_ids)
            ),
        )

    async def _load_request_usage(self, account_ids: list[str]) -> dict[str, AccountRequestUsage]:
        rows = await self._repo.list_request_usage_summary_by_account(account_ids)
        return {
            account_id: AccountRequestUsage(
                request_count=row.request_count,
                total_tokens=row.total_tokens,
                cached_input_tokens=row.cached_input_tokens,
                cache_creation_tokens=row.cache_creation_tokens,
                cache_read_tokens=row.cache_read_tokens,
                total_cost_usd=row.total_cost_usd,
            )
            for account_id, row in rows.items()
        }

    async def _additional_quotas_by_account(
        self, account_ids: list[str], account_id_set: set[str]
    ) -> dict[str, list[AccountAdditionalQuota]]:

        return await quota_cache.get(
            frozenset(account_ids),
            load=lambda: self._load_additional_quotas(account_ids, account_id_set),
            refresh=lambda: with_background_service(
                type(self), lambda service: service._load_additional_quotas(account_ids, account_id_set)
            ),
        )

    async def _load_additional_quotas(
        self, account_ids: list[str], account_id_set: set[str]
    ) -> dict[str, list[AccountAdditionalQuota]]:
        result: dict[str, list[AccountAdditionalQuota]] = {}
        additional_usage_repo = cast(AdditionalUsageRepository | None, self._additional_usage_repo)
        if additional_usage_repo:
            additional_quota_routing_overrides = await self._repo.additional_quota_routing_policy_overrides()
            quota_keys = [definition.quota_key for definition in list_additional_quota_definitions()]

            if _ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY not in quota_keys:
                quota_keys.append(_ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY)
            now_epoch = int(time.time())
            for quota_key in quota_keys:
                primary_entries = await additional_usage_repo.latest_by_account(
                    quota_key, "primary", account_ids=account_ids
                )
                secondary_entries = await additional_usage_repo.latest_by_account(
                    quota_key, "secondary", account_ids=account_ids
                )
                for account_id in (set(primary_entries) | set(secondary_entries)) & account_id_set:
                    primary_entry = primary_entries.get(account_id)
                    secondary_entry = secondary_entries.get(account_id)
                    reference_entry = primary_entry or secondary_entry
                    if reference_entry is None:
                        continue
                    result.setdefault(account_id, []).append(
                        AccountAdditionalQuota(
                            quota_key=quota_key,
                            limit_name=reference_entry.limit_name,
                            metered_feature=reference_entry.metered_feature,
                            display_label=get_additional_display_label_for_quota_key(quota_key)
                            or reference_entry.limit_name,
                            routing_policy=get_additional_quota_routing_policy(
                                quota_key,
                                overrides=additional_quota_routing_overrides,
                            ),
                            primary_window=_additional_quota_window(primary_entry, now_epoch=now_epoch),
                            secondary_window=_additional_quota_window(secondary_entry, now_epoch=now_epoch),
                        )
                    )
        for account_quota_list in result.values():
            account_quota_list.sort(key=lambda quota: quota.display_label or quota.quota_key or quota.limit_name)
        return result

    async def get_account_trends(self, account_id: str) -> AccountTrendsResponse | None:
        account = await self._get_visible_account(account_id)
        if not account or not self._usage_repo:
            return None
        now = utcnow()
        since = now - timedelta(days=_SPARKLINE_DAYS)
        since_epoch = naive_utc_to_epoch(since)
        bucket_count = (_SPARKLINE_DAYS * 24 * 3600) // _DETAIL_BUCKET_SECONDS
        buckets = await self._usage_repo.trends_by_bucket(
            since=since,
            bucket_seconds=_DETAIL_BUCKET_SECONDS,
            account_id=account_id,
        )
        trends = build_account_usage_trends(buckets, since_epoch, _DETAIL_BUCKET_SECONDS, bucket_count)
        trend = trends.get(account_id)
        return AccountTrendsResponse(
            account_id=account_id,
            primary=trend.primary if trend else [],
            secondary=trend.secondary if trend else [],
            secondary_scheduled=trend.secondary_scheduled if trend else [],
        )

    async def list_anthropic_models(self) -> AnthropicModelsResponse:

        cached = _anthropic_models
        if cached is not None and time.monotonic() - cached[0] < ANTHROPIC_MODELS_TTL_SECONDS:
            return AnthropicModelsResponse(models=cached[2], fetched_at=cached[1])

        async with _anthropic_models_refresh_lock():
            return await self._refresh_anthropic_models()

    async def _refresh_anthropic_models(self) -> AnthropicModelsResponse:
        global _anthropic_models
        cached = _anthropic_models
        if cached is not None and time.monotonic() - cached[0] < ANTHROPIC_MODELS_TTL_SECONDS:
            return AnthropicModelsResponse(models=cached[2], fetched_at=cached[1])
        error = "no Anthropic account can list models"
        accounts = [
            account
            for account in await self._repo.list_accounts()
            if normalize_provider_name(account.provider) == ANTHROPIC_PROVIDER_NAME
            and (account.status == AccountStatus.ACTIVE or account.status in _LIMITED_LISTABLE_STATUSES)
        ]
        accounts.sort(key=lambda account: account.status != AccountStatus.ACTIVE)
        for account in accounts[:ANTHROPIC_MODELS_MAX_ACCOUNTS]:
            try:
                fresh = account
                if account.status == AccountStatus.ACTIVE and self._auth_manager:
                    fresh = await self._auth_manager.ensure_fresh(account, force=False)
                    if fresh.status != AccountStatus.ACTIVE:
                        continue
                access_token = self._encryptor.decrypt(fresh.access_token_encrypted)
            except Exception as exc:
                error = f"account token unavailable ({type(exc).__name__})"
                continue
            status, ids = await probes.list_anthropic_models(
                access_token=access_token,
                base_url=get_settings().anthropic_upstream_base_url,
            )
            if ids:
                _anthropic_models = (time.monotonic(), utcnow(), ids)
                return AnthropicModelsResponse(models=ids, fetched_at=_anthropic_models[1])
            error = f"upstream model list returned HTTP {status}"
        if cached is not None:
            return AnthropicModelsResponse(models=cached[2], fetched_at=cached[1], stale=True, error=error)
        return AnthropicModelsResponse(error=error)
