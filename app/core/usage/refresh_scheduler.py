from __future__ import annotations

import asyncio
import contextlib
import importlib
import time
from dataclasses import dataclass, field
from datetime import timezone
from typing import cast

from app.core.audit.service import AuditDetails, AuditService
from app.core.auth.refresh import RefreshError
from app.core.balancer import TOKENS_LOST_REASON
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME, KIMI_PROVIDER_NAME, OPENAI_PROVIDER_NAME
from app.core.usage.refresh_leader import UsageLeaderRefresh as UsageLeaderRefresh
from app.core.usage.refresh_recovery import _BLOCK_RESET_MATCH_TOLERANCE_SECONDS as _BLOCK_RESET_MATCH_TOLERANCE_SECONDS
from app.core.usage.refresh_recovery import _RECOVERABLE_ACCOUNT_STATUSES as _RECOVERABLE_ACCOUNT_STATUSES
from app.core.usage.refresh_recovery import _T as _T
from app.core.usage.refresh_recovery import _background_accounts_repo as _background_accounts_repo
from app.core.usage.refresh_recovery import _BackgroundLimitWarmupRepository as _BackgroundLimitWarmupRepository
from app.core.usage.refresh_recovery import _BackgroundRequestLogsRepository as _BackgroundRequestLogsRepository
from app.core.usage.refresh_recovery import (
    _confirmed_free_monthly_reset_recovery as _confirmed_free_monthly_reset_recovery,
)
from app.core.usage.refresh_recovery import _invalidate_usage_refresh_caches as _invalidate_usage_refresh_caches
from app.core.usage.refresh_recovery import (
    _latest_confirmed_reset_transition_after_baseline as _latest_confirmed_reset_transition_after_baseline,
)
from app.core.usage.refresh_recovery import _LatestUsageRepository as _LatestUsageRepository
from app.core.usage.refresh_recovery import _LeaderElectionLike as _LeaderElectionLike
from app.core.usage.refresh_recovery import _MonthlyResetEvidence as _MonthlyResetEvidence
from app.core.usage.refresh_recovery import _openai_accounts as _openai_accounts
from app.core.usage.refresh_recovery import _ordered_usage_refresh_accounts as _ordered_usage_refresh_accounts
from app.core.usage.refresh_recovery import _RecoverableAccountsRepository as _RecoverableAccountsRepository
from app.core.usage.refresh_recovery import _resolve_monthly_reset_evidence as _resolve_monthly_reset_evidence
from app.core.usage.refresh_recovery import _select_long_window_entries as _select_long_window_entries
from app.core.usage.refresh_recovery import _select_long_window_entry as _select_long_window_entry
from app.core.usage.refresh_recovery import _usage_refresh_accounts as _usage_refresh_accounts
from app.core.usage.refresh_recovery import _usage_refresh_slice_seconds as _usage_refresh_slice_seconds
from app.core.usage.refresh_recovery import _warmup_eligible_accounts as _warmup_eligible_accounts
from app.core.usage.refresh_recovery import logger as logger
from app.core.usage.refresh_recovery import (
    reconcile_recoverable_account_statuses as reconcile_recoverable_account_statuses,
)
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.auth_manager import (
    AuthManager,
    _exchange_timeout_seconds,
    _refresh_token_material_fingerprint,
    is_locally_owned,
)
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.subscription_status import is_subscription_usable
from app.modules.limit_warmup.repository import LimitWarmupRepository
from app.modules.limit_warmup.service import LimitWarmupService, StreamingLimitWarmupSender
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.refresh_rules import USAGE_REFRESH_SINGLEFLIGHT
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from app.modules.usage.updater import UsageUpdater


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@dataclass(slots=True)
class UsageRefreshScheduler(UsageLeaderRefresh):
    interval_seconds: int
    enabled: bool
    _next_account_index: int = 0
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and (not self._task.done()):
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await USAGE_REFRESH_SINGLEFLIGHT.cancel_all()

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            started_at = time.monotonic()
            delay = await self._refresh_once()
            remaining_delay = max(0.0, delay - (time.monotonic() - started_at))
            if remaining_delay <= 0:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=remaining_delay)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> float:
        delay = await _get_leader_election().run_if_leader(self._refresh_cycle)
        return float(self.interval_seconds) if delay is None else delay

    async def _refresh_cycle(self) -> float:
        has_anthropic_accounts = await self._refresh_fork_accounts()
        delay = await self._refresh_as_leader()
        return min(delay, 10.0) if has_anthropic_accounts else delay

    async def _refresh_fork_accounts(self) -> bool:
        async with self._lock:
            audit_events: list[tuple[str, AuditDetails]] = []
            try:
                async with get_background_session() as session:
                    usage_repo = UsageRepository(session)
                    accounts_repo = AccountsRepository(session)
                    additional_usage_repo = AdditionalUsageRepository(session)
                    settings_repo = SettingsRepository(session)
                    warmup_repo = LimitWarmupRepository(session)
                    request_logs_repo = RequestLogsRepository(session)
                    all_accounts = await accounts_repo.list_accounts()
                    uncertain_accounts = [
                        Account(**{column.key: getattr(account, column.key) for column in Account.__table__.columns})
                        for account in all_accounts
                        if account.provider in {ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME, KIMI_PROVIDER_NAME}
                        and account.status == AccountStatus.EXCHANGE_UNCERTAIN
                        and is_locally_owned(account, get_settings())
                    ]
                    if uncertain_accounts:
                        auth_manager = AuthManager(accounts_repo)
                        settings = get_settings()
                        encryptor = TokenEncryptor()
                        for account in uncertain_accounts:
                            try:
                                if account.provider in settings.exchange_auto_reset_providers:
                                    result = await accounts_repo.auto_reset_exchange_intent(
                                        account.id,
                                        _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted),
                                        timeout_seconds=_exchange_timeout_seconds(),
                                        base_seconds=settings.exchange_auto_reset_base_seconds,
                                        max_attempts=settings.exchange_auto_reset_max_attempts,
                                    )
                                    details = {
                                        "account_id": account.id,
                                        "attempt": result.attempt,
                                        "prior_replay": result.prior_replay,
                                        "prior_reason": result.prior_reason,
                                    }
                                    if result.outcome == "reset":
                                        logger.info(
                                            "Uncertain exchange auto-reset account_id=%s attempt=%d prior_replay=%s prior_reason=%s",
                                            account.id,
                                            result.attempt,
                                            result.prior_replay,
                                            result.prior_reason,
                                        )
                                        audit_events.append(("account_exchange_auto_reset", details))
                                    elif result.outcome == "exhausted":
                                        logger.warning(
                                            "Uncertain exchange auto-recovery exhausted account_id=%s; exchange-reset or re-login needed",
                                            account.id,
                                        )
                                        audit_events.append(("account_exchange_auto_exhausted", details))
                                    elif result.outcome == "unsaved":
                                        await accounts_repo.update_status(
                                            account.id,
                                            AccountStatus.REAUTH_REQUIRED,
                                            TOKENS_LOST_REASON,
                                            expected_refresh_token_encrypted=account.refresh_token_encrypted,
                                        )
                                        logger.warning(
                                            "Uncertain exchange tokens lost before saving account_id=%s provider=%s; consumed refresh token not replayed, re-login required",
                                            account.id,
                                            account.provider,
                                        )
                                        audit_events.append(("account_exchange_unsaved_lost", details))
                                        get_account_selection_cache().invalidate()
                                        continue
                                await auth_manager.ensure_fresh(account, recover_uncertain=True)
                            except RefreshError as exc:
                                logger.debug(
                                    "Uncertain exchange recovery deferred account_id=%s code=%s", account.id, exc.code
                                )
                            except Exception:
                                logger.warning(
                                    "Uncertain exchange recovery failed account_id=%s", account.id, exc_info=True
                                )
                        all_accounts = await accounts_repo.list_accounts(refresh_existing=True)
                    accounts = [a for a in all_accounts if a.provider == ANTHROPIC_PROVIDER_NAME]
                    if not accounts:
                        return False
                    account_ids = [a.id for a in accounts]
                    before_primary = await usage_repo.latest_by_account(window="primary", account_ids=account_ids)
                    before_secondary = await usage_repo.latest_by_account(window="secondary", account_ids=account_ids)
                    updater = UsageUpdater(usage_repo, accounts_repo, additional_usage_repo)
                    now_epoch = utcnow().replace(tzinfo=timezone.utc).timestamp()
                    near_anthropic_reset = any(
                        (
                            account.provider == ANTHROPIC_PROVIDER_NAME
                            and is_subscription_usable(account)
                            and account.limit_warmup_enabled
                            and (account.status in {AccountStatus.ACTIVE, AccountStatus.RATE_LIMITED})
                            and ((entry := before_primary.get(account.id)) is not None)
                            and (entry.reset_at is not None)
                            and (now_epoch - 60 <= entry.reset_at <= now_epoch + 180)
                            for account in accounts
                        )
                    )
                    for account in accounts:
                        entry = before_primary.get(account.id)
                        if (
                            account.provider == ANTHROPIC_PROVIDER_NAME
                            and is_subscription_usable(account)
                            and (entry is not None)
                            and (entry.reset_at is not None)
                            and (entry.reset_at + 3 <= now_epoch)
                            and (entry.recorded_at.replace(tzinfo=timezone.utc).timestamp() < entry.reset_at + 3)
                        ):
                            await updater.force_refresh(account)
                    ordinary_usage_written = False
                    if not near_anthropic_reset:
                        ordinary_usage_written = await updater.refresh_accounts(
                            accounts, await usage_repo.latest_by_account(window="primary", account_ids=account_ids)
                        )
                    after_primary = await usage_repo.latest_by_account(window="primary", account_ids=account_ids)
                    after_secondary = await usage_repo.latest_by_account(window="secondary", account_ids=account_ids)
                    anthropic_standard = await additional_usage_repo.latest_by_account("anthropic_standard", "primary")
                    refreshed_accounts = [
                        a
                        for a in await accounts_repo.list_accounts(refresh_existing=True)
                        if a.provider == ANTHROPIC_PROVIDER_NAME
                    ]
                    await reconcile_recoverable_account_statuses(
                        accounts_repo=accounts_repo, usage_repo=usage_repo, accounts=refreshed_accounts
                    )
                    refreshed_accounts = [
                        a
                        for a in await accounts_repo.list_accounts(refresh_existing=True)
                        if a.provider == ANTHROPIC_PROVIDER_NAME
                    ]
                    warmup_accounts = _warmup_eligible_accounts(refreshed_accounts)
                    if not ordinary_usage_written:
                        warmup_accounts = [
                            account for account in warmup_accounts if account.provider == ANTHROPIC_PROVIDER_NAME
                        ]
                    if warmup_accounts:
                        dashboard_settings = await settings_repo.get_or_create()
                        planner_settings = await QuotaPlannerRepository(session).get_settings()
                        accounts_by_id = {account.id: account for account in refreshed_accounts}

                        async def confirm_anthropic_prime(account_id: str, sent_at: int) -> bool:
                            account = accounts_by_id[account_id]
                            for _ in range(3):
                                await updater.force_refresh(account)
                                observed = await usage_repo.latest_entry_for_account(account_id, window="primary")
                                if observed is not None and observed.reset_at is not None:
                                    if sent_at + 4 * 3600 <= observed.reset_at <= sent_at + 6 * 3600:
                                        return True
                                await asyncio.sleep(2)
                            return False

                        warmup_service = LimitWarmupService(
                            warmup_repo,
                            request_logs_repo,
                            sender=StreamingLimitWarmupSender(
                                accounts_repo, accounts_repo_factory=_background_accounts_repo
                            ),
                            confirm_anthropic_prime=confirm_anthropic_prime,
                        )
                        await warmup_service.run_after_usage_refresh(
                            accounts=warmup_accounts,
                            settings=dashboard_settings,
                            before_primary=before_primary,
                            before_secondary=before_secondary,
                            after_primary=after_primary,
                            after_secondary=after_secondary,
                            anthropic_standard=anthropic_standard,
                            planner_settings=planner_settings,
                        )
                    await get_rate_limit_headers_cache().invalidate()
                    get_account_selection_cache().invalidate()
            except Exception:
                logger.exception("Usage refresh loop failed")
            finally:
                for action, details in audit_events:
                    AuditService.log_async(action, details=details)
        return True

    def _select_next_account(self, accounts: list[Account]) -> tuple[Account | None, bool]:
        if not accounts:
            self._next_account_index = 0
            return (None, True)
        index = self._next_account_index % len(accounts)
        next_index = (index + 1) % len(accounts)
        self._next_account_index = next_index
        return (accounts[index], next_index == 0)


def build_usage_refresh_scheduler() -> UsageRefreshScheduler:
    settings = get_settings()
    return UsageRefreshScheduler(
        interval_seconds=min(settings.usage_refresh_interval_seconds, 10), enabled=settings.usage_refresh_enabled
    )
