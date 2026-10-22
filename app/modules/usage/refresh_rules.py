from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.balancer import PERMANENT_FAILURE_CODES, QUOTA_EXCEEDED_COOLDOWN_SECONDS
from app.core.clients.anthropic_usage import fetch_anthropic_usage
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings import get_settings
from app.core.plan_types import account_plan_matches_allowed, coerce_account_plan_type
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME
from app.core.upstream_proxy import ResolvedUpstreamRoute, resolve_upstream_route
from app.core.usage.models import AdditionalRateLimitPayload, UsagePayload, UsageWindow
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.db.usage_models import UsageHistory
from app.modules.usage.additional_quota_keys import canonicalize_additional_quota_key
from app.modules.usage.refresh_ports import AccountRefreshResult, AdditionalUsageRepositoryPort
from app.modules.usage.repository import AdditionalUsageRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _MergedAdditionalWindow:
    limit_name: str
    metered_feature: str
    used_percent: float
    reset_at: int | None
    window_minutes: int | None


last_successful_refresh: dict[str, datetime] = {}


usage_refresh_auth_cooldowns: dict[str, float] = {}


usage_refresh_rate_limit_strikes: dict[str, int] = {}


_USAGE_REFRESH_RATE_LIMIT_BASE_SECONDS = 60.0


_USAGE_REFRESH_RATE_LIMIT_MAX_SECONDS = 900.0


USAGE_REFRESH_PROVIDERS = frozenset({ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME})


ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"


ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW = "primary"


class _UsageRefreshSingleflight:
    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[AccountRefreshResult]] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        account_id: str,
        factory: Callable[[], Awaitable[AccountRefreshResult]],
        *,
        join_existing: bool = True,
    ) -> AccountRefreshResult:
        while True:
            wait_for_existing: asyncio.Task[AccountRefreshResult] | None = None
            async with self._lock:
                task = self._inflight.get(account_id)
                if task is None or task.done():
                    task = asyncio.create_task(self._run_factory(factory))
                    self._inflight[account_id] = task
                    task.add_done_callback(
                        lambda done, *, key=account_id: self._clear_if_current(key, done),
                    )
                    break
                if join_existing:
                    break
                wait_for_existing = task
            if wait_for_existing is None:
                break
            try:
                await asyncio.shield(wait_for_existing)
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
            except Exception:
                pass
        return await asyncio.shield(task)

    async def _run_factory(
        self,
        factory: Callable[[], Awaitable[AccountRefreshResult]],
    ) -> AccountRefreshResult:
        return await factory()

    def _clear_if_current(
        self,
        key: str,
        task: asyncio.Task[AccountRefreshResult],
    ) -> None:
        current = self._inflight.get(key)
        if current is task:
            self._inflight.pop(key, None)
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            task.exception()

    def clear(self) -> None:
        self._inflight.clear()

    async def cancel_all(self) -> None:
        async with self._lock:
            tasks = list(self._inflight.values())
            self._inflight.clear()
        for task in tasks:
            task.cancel()
        if not tasks:
            return
        with contextlib.suppress(BaseException):
            await asyncio.gather(*tasks, return_exceptions=True)


USAGE_REFRESH_SINGLEFLIGHT = _UsageRefreshSingleflight()


def copy_refresh_sensitive_account_state(account: Account, stored: Account) -> None:
    account.chatgpt_account_id = stored.chatgpt_account_id
    account.email = stored.email
    account.workspace_id = stored.workspace_id
    account.workspace_label = stored.workspace_label
    account.seat_type = stored.seat_type
    account.plan_type = stored.plan_type
    account.access_token_encrypted = stored.access_token_encrypted
    account.refresh_token_encrypted = stored.refresh_token_encrypted
    account.id_token_encrypted = stored.id_token_encrypted
    account.last_refresh = stored.last_refresh
    account.status = stored.status
    account.deactivation_reason = stored.deactivation_reason
    account.reset_at = stored.reset_at
    account.blocked_at = stored.blocked_at


def credits_snapshot(payload: UsagePayload) -> tuple[bool | None, bool | None, float | None]:
    credits = payload.credits
    if credits is None:
        return None, None, None
    credits_has = credits.has_credits
    credits_unlimited = credits.unlimited
    balance_value = credits.balance
    return credits_has, credits_unlimited, _parse_credits_balance(balance_value)


async def fetch_usage_payload(
    *,
    account: Account,
    access_token: str,
    usage_account_id: str | None,
) -> UsagePayload:
    if account.provider == ANTHROPIC_PROVIDER_NAME:
        return await fetch_anthropic_usage(access_token=access_token, account_id=account.id)
    route = await _resolve_upstream_route_for_account(account, operation="usage_refresh")
    return await fetch_usage(
        access_token=access_token,
        account_id=usage_account_id,
        route=route,
        allow_direct_egress=route is None,
    )


def payload_mismatches_account_slot(account: Account, payload: UsagePayload) -> bool:
    payload_workspace_id = clean_optional(payload.workspace_id)
    if account.workspace_id and payload_workspace_id and account.workspace_id != payload_workspace_id:
        return True
    if not payload_workspace_id:
        payload_plan_type = coerce_account_plan_type(payload.plan_type, account.plan_type or "free")
        stored_plan_type = coerce_account_plan_type(account.plan_type, "free")
        if (
            payload.plan_type
            and stored_plan_type not in {"unknown", ""}
            and not account_plan_matches_allowed(payload_plan_type, {stored_plan_type})
        ):
            return True
    return False


def clean_optional(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def usage_entry_written(entry: UsageHistory | None) -> bool:
    return entry is not None


def window_has_available_quota(window: UsageWindow) -> bool:
    used_percent = window.used_percent
    return used_percent is not None and float(used_percent) < 100.0


def window_is_exhausted(window: UsageWindow) -> bool:
    used_percent = window.used_percent
    return used_percent is not None and float(used_percent) >= 100.0


def _prefer_merged_additional_window(
    existing: _MergedAdditionalWindow,
    candidate: _MergedAdditionalWindow,
    *,
    quota_key: str,
    window: str,
) -> _MergedAdditionalWindow:
    if candidate.used_percent > existing.used_percent:
        logger.warning(
            "Additional usage refresh saw conflicting aliases for the same canonical quota window; "
            "keeping the higher usage sample account_quota=%s window=%s existing_limit=%s candidate_limit=%s "
            "request_id=%s",
            quota_key,
            window,
            existing.limit_name,
            candidate.limit_name,
            get_request_id(),
        )
        return candidate
    if candidate.used_percent < existing.used_percent:
        logger.warning(
            "Additional usage refresh saw conflicting aliases for the same canonical quota window; "
            "keeping the higher usage sample account_quota=%s window=%s existing_limit=%s candidate_limit=%s "
            "request_id=%s",
            quota_key,
            window,
            existing.limit_name,
            candidate.limit_name,
            get_request_id(),
        )
        return existing
    preferred = sorted(
        (existing, candidate),
        key=lambda entry: (entry.limit_name, entry.metered_feature),
    )[0]
    if preferred != existing or existing != candidate:
        logger.info(
            "Additional usage refresh coalesced duplicate aliases for canonical quota window "
            "account_quota=%s window=%s chosen_limit=%s request_id=%s",
            quota_key,
            window,
            preferred.limit_name,
            get_request_id(),
        )
    return preferred


def merge_additional_rate_limits(
    additional_rate_limits: Collection[AdditionalRateLimitPayload],
    *,
    account_id: str,
    now_epoch: int,
) -> dict[str, dict[str, _MergedAdditionalWindow]]:
    merged: dict[str, dict[str, _MergedAdditionalWindow]] = {}
    for additional in additional_rate_limits:
        limit_name = getattr(additional, "limit_name", None)
        metered_feature = getattr(additional, "metered_feature", None)
        quota_key = canonicalize_additional_quota_key(
            limit_name=limit_name,
            metered_feature=metered_feature,
        )
        if quota_key is None:
            logger.warning(
                "Skipping additional usage item without resolvable quota key "
                "account_id=%s limit_name=%s metered_feature=%s request_id=%s",
                account_id,
                limit_name,
                metered_feature,
                get_request_id(),
            )
            continue
        rate_limit = getattr(additional, "rate_limit", None)
        if rate_limit is None:
            continue
        for window_name, usage_window in (
            ("primary", getattr(rate_limit, "primary_window", None)),
            ("secondary", getattr(rate_limit, "secondary_window", None)),
        ):
            if usage_window is None or usage_window.used_percent is None:
                continue
            candidate = _MergedAdditionalWindow(
                limit_name=str(limit_name),
                metered_feature=str(metered_feature),
                used_percent=float(usage_window.used_percent),
                reset_at=bounded_exhaustion_reset_at(
                    usage_window.used_percent,
                    usage_window.reset_at,
                    usage_window.reset_after_seconds,
                    usage_window.limit_window_seconds,
                    now_epoch,
                ),
                window_minutes=minutes_of(usage_window.limit_window_seconds),
            )
            windows = merged.setdefault(quota_key, {})
            existing = windows.get(window_name)
            windows[window_name] = (
                candidate
                if existing is None
                else _prefer_merged_additional_window(
                    existing,
                    candidate,
                    quota_key=quota_key,
                    window=window_name,
                )
            )
    return merged


async def add_additional_usage_entry(
    repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository,
    *,
    account_id: str,
    limit_name: str,
    metered_feature: str,
    quota_key: str,
    window: str,
    used_percent: float,
    reset_at: int | None,
    window_minutes: int | None,
) -> None:
    await repo.add_entry(
        account_id=account_id,
        limit_name=limit_name,
        metered_feature=metered_feature,
        quota_key=quota_key,
        window=window,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes,
    )


async def list_additional_usage_quota_keys(
    repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository,
    *,
    account_ids: Collection[str] | None = None,
) -> list[str]:
    return await repo.list_quota_keys(account_ids=account_ids)


async def delete_additional_usage_quota_key(
    repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository,
    account_id: str,
    quota_key: str,
) -> None:
    await repo.delete_for_account_and_quota_key(account_id, quota_key)


async def delete_additional_usage_quota_key_window(
    repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository,
    account_id: str,
    quota_key: str,
    window: str,
) -> None:
    await repo.delete_for_account_quota_key_window(account_id, quota_key, window)


def latest_usage_is_fresh(
    latest: UsageHistory | None,
    *,
    now: datetime,
    interval_seconds: int,
) -> bool:
    if latest is None:
        return False
    if (now - latest.recorded_at).total_seconds() >= interval_seconds:
        return False
    if latest.reset_at is not None:
        now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
        if now_epoch >= latest.reset_at:
            return False
    return True


def quota_recovery_should_bypass_freshness(account: Account, *, latest: UsageHistory | None) -> bool:
    if _account_needs_post_reset_refresh(account, latest=latest):
        return True
    if account.status != AccountStatus.QUOTA_EXCEEDED:
        return False
    if account.blocked_at is None:
        return latest is None
    cooldown_expires_at = account.blocked_at + QUOTA_EXCEEDED_COOLDOWN_SECONDS
    if time.time() < cooldown_expires_at:
        return False
    if latest is None:
        return True
    recorded_at = latest.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    return recorded_at.timestamp() < cooldown_expires_at


def _account_needs_post_reset_refresh(account: Account, *, latest: UsageHistory | None) -> bool:
    if account.status not in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED):
        return False
    if account.reset_at is None:
        return False
    if time.time() < account.reset_at:
        return False
    if latest is None:
        return True
    recorded_at = latest.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    return recorded_at.timestamp() < float(account.reset_at)


def _parse_credits_balance(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def minutes_of(limit_seconds: int | None) -> int | None:
    if not limit_seconds or limit_seconds <= 0:
        return None
    return max(1, math.ceil(limit_seconds / 60))


def epoch_now() -> int:
    return int(utcnow().replace(tzinfo=timezone.utc).timestamp())


def reset_time(reset_at: int | None, reset_after_seconds: int | None, now_epoch: int) -> int | None:
    if reset_at is not None:
        return int(reset_at)
    if reset_after_seconds is None:
        return None
    return now_epoch + max(0, int(reset_after_seconds))


_DEFAULT_EXHAUSTION_HORIZON_SECONDS = 3600


def bounded_exhaustion_reset_at(
    used_percent: float | None,
    reset_at: int | None,
    reset_after_seconds: int | None,
    limit_window_seconds: int | None,
    now_epoch: int,
) -> int | None:

    resolved = reset_time(reset_at, reset_after_seconds, now_epoch)
    if resolved is not None:
        return resolved
    if used_percent is None or float(used_percent) < 100.0:
        return None
    horizon = (
        int(limit_window_seconds)
        if limit_window_seconds is not None and int(limit_window_seconds) > 0
        else _DEFAULT_EXHAUSTION_HORIZON_SECONDS
    )
    return now_epoch + horizon


_DEACTIVATING_USAGE_STATUS_CODES = {402, 404}


_DEACTIVATING_USAGE_MESSAGE_HINTS = (
    "your openai account has been deactivated",
    "account has been deactivated",
)


def should_deactivate_for_usage_error(exc: UsageFetchError) -> bool:
    if exc.status_code in _DEACTIVATING_USAGE_STATUS_CODES:
        return True
    if exc.code in PERMANENT_FAILURE_CODES:
        return True
    lowered = exc.message.lower()
    return any(hint in lowered for hint in _DEACTIVATING_USAGE_MESSAGE_HINTS)


async def _resolve_upstream_route_for_account(account: Account, *, operation: str) -> ResolvedUpstreamRoute | None:
    async with get_background_session() as session:
        return await resolve_upstream_route(
            session,
            account_id=account.id,
            operation=operation,
            scope="account",
        )


def mark_usage_refresh_auth_cooldown(account_id: str, status_code: int) -> None:
    if status_code == 429:
        strikes = usage_refresh_rate_limit_strikes.get(account_id, 0) + 1
        usage_refresh_rate_limit_strikes[account_id] = strikes
        delay = min(
            _USAGE_REFRESH_RATE_LIMIT_MAX_SECONDS,
            _USAGE_REFRESH_RATE_LIMIT_BASE_SECONDS * (2 ** min(strikes - 1, 10)) * random.uniform(0.8, 1.2),
        )
        usage_refresh_auth_cooldowns[account_id] = time.monotonic() + delay
        return
    if status_code not in {401, 403}:
        return
    cooldown_seconds = max(0.0, float(get_settings().usage_refresh_auth_failure_cooldown_seconds))
    if cooldown_seconds <= 0:
        return
    usage_refresh_auth_cooldowns[account_id] = time.monotonic() + cooldown_seconds


def is_usage_refresh_in_cooldown(account_id: str) -> bool:
    expires_at = usage_refresh_auth_cooldowns.get(account_id)
    if expires_at is None:
        return False
    if expires_at > time.monotonic():
        return True
    usage_refresh_auth_cooldowns.pop(account_id, None)
    return False


def clear_usage_refresh_auth_cooldown(account_id: str) -> None:
    usage_refresh_auth_cooldowns.pop(account_id, None)
    usage_refresh_rate_limit_strikes.pop(account_id, None)


def prune_usage_refresh_auth_cooldowns() -> None:
    now = time.monotonic()
    stale = [account_id for account_id, expires_at in usage_refresh_auth_cooldowns.items() if expires_at <= now]
    for account_id in stale:
        usage_refresh_auth_cooldowns.pop(account_id, None)


def _clear_usage_refresh_state() -> None:
    usage_refresh_auth_cooldowns.clear()
    usage_refresh_rate_limit_strikes.clear()
    last_successful_refresh.clear()
    USAGE_REFRESH_SINGLEFLIGHT.clear()
