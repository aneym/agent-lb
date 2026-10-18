from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from app.core.providers import (
    ANTHROPIC_COMPAT_PROFILES,
    normalize_provider_name,
)
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts import probes
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

_FABLE_PROBE_MODEL = "claude-fable-5-1"


ANTHROPIC_FABLE_ACCESS_QUOTA_KEY = "anthropic_fable_access"
_ANTHROPIC_FABLE_PROBE_FEATURE = "anthropic_fable_probe"
_ANTHROPIC_FABLE_PROBE_WINDOW = "primary"
_ANTHROPIC_COOLDOWN_FEATURE = "anthropic_messages"
_ANTHROPIC_COOLDOWN_WINDOW = "primary"
_ANTHROPIC_FABLE_QUOTA_KEYS = ("anthropic_top", "anthropic_top_thinking", "anthropic_opus", "anthropic_opus_thinking")


@dataclass(frozen=True, slots=True)
class AnthropicQuotaCooldown:
    quota_key: str
    row_id: int


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


class _AccountsRepositoryLike(Protocol):
    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]: ...

    async def get_by_id(self, account_id: str) -> Account | None: ...

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None = None,
    ) -> bool: ...

    async def update_subscription_ledger(
        self,
        account_id: str,
        *,
        status: str | None,
        next_charge_at: datetime | None,
        current_period_end_at: datetime | None,
        amount: float | None,
        currency: str | None,
        last_verified_at: datetime | None,
        notes: str | None,
    ) -> bool: ...


class _AuthManagerLike(Protocol):
    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account: ...


async def _default_probe_sender(account: Account, access_token: str) -> tuple[int, str | None]:
    from app.core.config.settings import get_settings

    settings = get_settings()
    provider = normalize_provider_name(account.provider)
    compat_profiles = {profile.provider_name: profile for profile in ANTHROPIC_COMPAT_PROFILES}
    compat_profile = compat_profiles.get(provider)
    if compat_profile is not None:
        base_url = str(getattr(settings, compat_profile.upstream_settings_attr))
        return await probes.send_messages_probe(
            access_token=access_token,
            base_url=base_url,
            model=compat_profile.default_probe_model,
        )
    return await probes.send_openai_probe_with_error_code(
        access_token=access_token,
        chatgpt_account_id=account.chatgpt_account_id,
        model=probes.DEFAULT_PROBE_MODEL,
    )


async def _default_openai_auth_check_sender(account: Account, access_token: str) -> int:
    return await probes.send_openai_auth_check(
        access_token=access_token,
        chatgpt_account_id=account.chatgpt_account_id,
    )


async def _default_fable_probe_sender(account: Account, access_token: str) -> tuple[int, str | None]:

    del account
    from app.core.config.settings import get_settings

    settings = get_settings()
    return await probes.send_messages_probe(
        access_token=access_token,
        base_url=settings.anthropic_upstream_base_url,
        model=_FABLE_PROBE_MODEL,
    )


async def _default_quota_cooldown_probe_sender(
    account: Account,
    access_token: str,
    quota_key: str,
) -> tuple[int, str | None]:
    del account
    from app.core.config.settings import get_settings

    settings = get_settings()
    thinking: probes.AdaptiveThinking | None = None
    max_tokens = 4
    if quota_key in ("anthropic_top_thinking", "anthropic_opus_thinking"):
        thinking = {"type": "adaptive"}
        max_tokens = 32
    elif quota_key not in ("anthropic_top", "anthropic_opus"):
        raise ValueError(f"Unsupported Anthropic cooldown quota key: {quota_key}")
    return await probes.send_messages_probe(
        access_token=access_token,
        base_url=settings.anthropic_upstream_base_url,
        model="claude-opus-5-5" if quota_key.startswith("anthropic_opus") else _FABLE_PROBE_MODEL,
        max_tokens=max_tokens,
        thinking=thinking,
    )


async def _default_active_quota_cooldown_lookup(account_id: str) -> list[AnthropicQuotaCooldown]:
    now_epoch = naive_utc_to_epoch(utcnow())
    active: list[AnthropicQuotaCooldown] = []
    async with get_background_session() as session:
        repo = AdditionalUsageRepository(session)
        for quota_key in _ANTHROPIC_FABLE_QUOTA_KEYS:
            latest = await repo.latest_by_quota_key(
                quota_key,
                _ANTHROPIC_COOLDOWN_WINDOW,
                account_ids=[account_id],
            )
            entry = latest.get(account_id)
            if (
                entry is not None
                and float(entry.used_percent) >= 100.0
                and entry.reset_at is not None
                and int(entry.reset_at) > now_epoch
            ):
                active.append(AnthropicQuotaCooldown(quota_key=quota_key, row_id=int(entry.id)))
    return active


async def _default_quota_cooldown_clearer(account_id: str, quota_key: str, expected_id: int) -> bool:
    async with get_background_session() as session:
        repo = AdditionalUsageRepository(session)
        return await repo.clear_cooldown_if_latest(
            account_id,
            quota_key,
            _ANTHROPIC_COOLDOWN_WINDOW,
            expected_id,
            metered_feature=_ANTHROPIC_COOLDOWN_FEATURE,
        )


async def _default_weekly_usage_lookup(account_id: str) -> tuple[float, int | None] | None:
    async with get_background_session() as session:
        repo = UsageRepository(session)
        entry = await repo.latest_entry_for_account(account_id, window="secondary")
        if entry is None:
            return None
        return float(entry.used_percent), (int(entry.reset_at) if entry.reset_at is not None else None)


async def _default_fable_marker_writer(account_id: str, used_percent: float, reset_at: int | None) -> None:
    now_epoch = naive_utc_to_epoch(utcnow())
    window_minutes = max(1, int((reset_at - now_epoch + 59) / 60)) if reset_at is not None else None
    async with get_background_session() as session:
        repo = AdditionalUsageRepository(session)
        await repo.add_entry(
            account_id,
            limit_name=ANTHROPIC_FABLE_ACCESS_QUOTA_KEY,
            metered_feature=_ANTHROPIC_FABLE_PROBE_FEATURE,
            quota_key=ANTHROPIC_FABLE_ACCESS_QUOTA_KEY,
            window=_ANTHROPIC_FABLE_PROBE_WINDOW,
            used_percent=used_percent,
            reset_at=reset_at,
            window_minutes=window_minutes,
        )


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@asynccontextmanager
async def _default_accounts_repo_factory() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        yield AccountsRepository(session)


def _default_auth_manager_factory(repo: _AccountsRepositoryLike) -> _AuthManagerLike:
    return AuthManager(cast(AccountsRepository, repo), refresh_repo_factory=_default_accounts_repo_factory)
