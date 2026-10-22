from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.core.audit.service import AuditService
from app.core.auth.refresh import RefreshError, classify_refresh_error
from app.core.balancer import ACCOUNT_ON_HOLD_REASON, DEAD_REFRESH_GRANT_REASONS
from app.core.crypto import TokenEncryptor
from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    normalize_provider_name,
)
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts import probes
from app.modules.accounts.auth_contracts import is_locally_owned
from app.modules.accounts.pulse_backend import (
    AnthropicQuotaCooldown,
    _AccountsRepositoryLike,
    _AuthManagerLike,
    _default_accounts_repo_factory,
    _default_active_quota_cooldown_lookup,
    _default_auth_manager_factory,
    _default_fable_marker_writer,
    _default_fable_probe_sender,
    _default_openai_auth_check_sender,
    _default_probe_sender,
    _default_quota_cooldown_clearer,
    _default_quota_cooldown_probe_sender,
    _default_weekly_usage_lookup,
    _get_leader_election,
    _LeaderElectionLike,
)
from app.modules.accounts.pulse_verdicts import apply_verdict, mark_disconnected
from app.modules.accounts.subscription_status import (
    CANCELED_SUBSCRIPTION_STATUS,
    is_subscription_usable,
    normalize_subscription_status,
)
from app.modules.proxy.account_cache import get_account_selection_cache

logger = logging.getLogger(__name__)
_ON_HOLD_MIN_RETRY_SECONDS = 3600.0


class FableProbeVerdict(str, Enum):
    CAPABLE = "capable"
    REFUSED = "refused"
    INCONCLUSIVE = "inconclusive"


def _classify_fable_probe(status: int, message: str | None) -> FableProbeVerdict:

    del message
    if 200 <= status < 300:
        return FableProbeVerdict.CAPABLE
    if status in (400, 403, 404):
        return FableProbeVerdict.REFUSED
    return FableProbeVerdict.INCONCLUSIVE


def _is_fable_probe_routable(account: Account) -> bool:

    return account.status not in (
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.DEACTIVATED,
        AccountStatus.PAUSED,
    ) and is_subscription_usable(account)


_RepoFactory = Callable[[], AbstractAsyncContextManager[_AccountsRepositoryLike]]
_AuthManagerFactory = Callable[[_AccountsRepositoryLike], _AuthManagerLike]
_LeaderElectionFactory = Callable[[], _LeaderElectionLike]
_ProbeSender = Callable[[Account, str], Awaitable[tuple[int, str | None]]]

_AuthCheckSender = Callable[[Account, str], Awaitable[int]]


_WeeklyUsageLookup = Callable[[str], Awaitable[tuple[float, int | None] | None]]
_FableMarkerWriter = Callable[[str, float, int | None], Awaitable[None]]
_ActiveQuotaCooldownLookup = Callable[[str], Awaitable[list[AnthropicQuotaCooldown]]]
_QuotaCooldownProbeSender = Callable[[Account, str, str], Awaitable[tuple[int, str | None]]]
_QuotaCooldownClearer = Callable[[str, str, int], Awaitable[bool]]


@dataclass(slots=True)
class _FailureBackoff:
    attempts: int
    recorded_monotonic: float
    retry_after_monotonic: float


@dataclass(slots=True)
class AccountPulseScheduler:
    interval_seconds: int
    enabled: bool
    concurrency: int
    jitter_seconds: float
    recovery_interval_seconds: int = 900
    failure_backoff_base_seconds: float = 600.0
    failure_backoff_max_seconds: float = 21600.0
    leader_election_factory: _LeaderElectionFactory = field(default_factory=lambda: _get_leader_election)
    repo_factory: _RepoFactory = field(default_factory=lambda: _default_accounts_repo_factory)
    auth_manager_factory: _AuthManagerFactory = field(default_factory=lambda: _default_auth_manager_factory)
    probe_sender: _ProbeSender = field(default_factory=lambda: _default_probe_sender)
    openai_auth_check_sender: _AuthCheckSender = field(default_factory=lambda: _default_openai_auth_check_sender)
    fable_probe_sender: _ProbeSender = field(default_factory=lambda: _default_fable_probe_sender)
    weekly_usage_lookup: _WeeklyUsageLookup = field(default_factory=lambda: _default_weekly_usage_lookup)
    fable_marker_writer: _FableMarkerWriter = field(default_factory=lambda: _default_fable_marker_writer)
    active_quota_cooldown_lookup: _ActiveQuotaCooldownLookup = field(
        default_factory=lambda: _default_active_quota_cooldown_lookup
    )
    quota_cooldown_probe_sender: _QuotaCooldownProbeSender = field(
        default_factory=lambda: _default_quota_cooldown_probe_sender
    )
    quota_cooldown_clearer: _QuotaCooldownClearer = field(default_factory=lambda: _default_quota_cooldown_clearer)
    sleep: Callable[[float], Awaitable[None]] = field(default_factory=lambda: asyncio.sleep)
    now: Callable[[], datetime] = field(default_factory=lambda: utcnow)
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _failures: dict[str, _FailureBackoff] = field(default_factory=dict)
    _encryptor: TokenEncryptor = field(default_factory=TokenEncryptor)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and not self._task.done():
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

    async def _run_loop(self) -> None:

        wake_seconds = min(self.interval_seconds, self.recovery_interval_seconds)
        next_full_pass_monotonic = time.monotonic()
        while not self._stop.is_set():
            jitter = _jitter_delay(self.jitter_seconds)
            if jitter > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=jitter)
                    break
                except asyncio.TimeoutError:
                    pass
            full_pass = time.monotonic() >= next_full_pass_monotonic
            try:
                if full_pass:
                    await self.pulse_once()
                else:
                    await self.recovery_pulse_once()
            except Exception:
                logger.exception("Account pulse pass failed")
            if full_pass:
                next_full_pass_monotonic = time.monotonic() + self.interval_seconds
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wake_seconds)
            except asyncio.TimeoutError:
                continue

    async def pulse_once(self) -> None:
        await self._pulse_pass(recovery_only=False)

    async def recovery_pulse_once(self) -> None:
        await self._pulse_pass(recovery_only=True)

    async def _pulse_pass(self, *, recovery_only: bool) -> None:
        if not await self.leader_election_factory().try_acquire():
            return
        async with self._lock:
            async with self.repo_factory() as repo:
                accounts = await repo.list_accounts(refresh_existing=True)
                candidate_ids = [
                    account.id for account in accounts if self._is_pulse_candidate(account, recovery_only=recovery_only)
                ]
            if not candidate_ids:
                return
            semaphore = asyncio.Semaphore(max(1, self.concurrency))

            results = await asyncio.gather(
                *(self._pulse_candidate(account_id, semaphore) for account_id in candidate_ids),
                return_exceptions=True,
            )
            for account_id, result in zip(candidate_ids, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        "Account pulse candidate crashed account_id=%s",
                        account_id,
                        exc_info=result,
                    )

    def _is_pulse_candidate(self, account: Account, *, recovery_only: bool) -> bool:
        if account.status == AccountStatus.PAUSED:
            return False
        if account.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            if account.deactivation_reason in DEAD_REFRESH_GRANT_REASONS:
                return account.id not in self._failures
            if account.deactivation_reason == ACCOUNT_ON_HOLD_REASON:
                return not self._in_backoff(account.id, min_delay_seconds=_ON_HOLD_MIN_RETRY_SECONDS)
        if not recovery_only:
            return not self._in_backoff(account.id)
        if not _is_recovery_pending(account):
            return False

        return not self._in_backoff(account.id, max_delay_seconds=float(self.recovery_interval_seconds))

    async def _pulse_candidate(self, account_id: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            async with self.repo_factory() as repo:
                account = await repo.get_by_id(account_id)
                if account is None or account.status == AccountStatus.PAUSED:
                    return
                from app.core.config.settings import get_settings

                if not is_locally_owned(account, get_settings()):
                    logger.debug(
                        "Account pulse skipping non-locally-owned account_id=%s owner_instance=%s",
                        account_id,
                        account.owner_instance,
                    )
                    return
                manager = self.auth_manager_factory(repo)
                try:
                    fresh_account = await manager.ensure_fresh(account, force=False)
                except RefreshError as exc:
                    is_permanent = exc.is_permanent or classify_refresh_error(exc.code)
                    if is_permanent and account.status == AccountStatus.ACTIVE:
                        await mark_disconnected(
                            repo,
                            account,
                            reason=f"Account pulse: token refresh failed ({exc.code})",
                        )
                    else:
                        self._record_failure(account_id)
                    return
                except Exception as exc:
                    self._record_failure(account_id)
                    logger.warning(
                        "Account pulse refresh failed account_id=%s error_type=%s",
                        account_id,
                        exc.__class__.__name__,
                        exc_info=True,
                    )
                    return
                access_token = self._encryptor.decrypt(fresh_account.access_token_encrypted)
                status, message = await self.probe_sender(fresh_account, access_token)
                verdict = probes.classify_probe_result(status, message)
                if (
                    verdict is probes.ProbeVerdict.DISCONNECTED
                    and normalize_provider_name(fresh_account.provider) == OPENAI_PROVIDER_NAME
                ):
                    verdict = await self._confirm_openai_rejection(fresh_account, access_token, status, message)
                await apply_verdict(
                    repo,
                    fresh_account,
                    verdict,
                    status,
                    message,
                    now=self.now,
                    failures=self._failures,
                    record_failure=self._record_failure,
                )

                await self._maybe_probe_fable_access(fresh_account, access_token)
                await self._reconcile_fable_quota_cooldowns(fresh_account, access_token)

    async def _confirm_openai_rejection(
        self,
        account: Account,
        access_token: str,
        status: int,
        code: str | None,
    ) -> probes.ProbeVerdict:

        check_status = await self.openai_auth_check_sender(account, access_token)
        if check_status == 401:
            return probes.ProbeVerdict.DISCONNECTED
        logger.warning(
            "Account pulse probe got HTTP %s code=%s but the auth check returned HTTP %s; "
            "not marking the credential rejected account_id=%s",
            status,
            code,
            check_status,
            account.id,
        )
        return probes.ProbeVerdict.INCONCLUSIVE

    async def _reconcile_fable_quota_cooldowns(self, account: Account, access_token: str) -> None:

        try:
            if normalize_provider_name(account.provider) != ANTHROPIC_PROVIDER_NAME:
                return
            if not _is_fable_probe_routable(account):
                return
            cooldowns = await self.active_quota_cooldown_lookup(account.id)
            for cooldown in cooldowns:
                status, _message = await self.quota_cooldown_probe_sender(
                    account,
                    access_token,
                    cooldown.quota_key,
                )
                if not 200 <= status < 300:
                    continue
                cleared = await self.quota_cooldown_clearer(
                    account.id,
                    cooldown.quota_key,
                    cooldown.row_id,
                )
                if cleared:
                    get_account_selection_cache().invalidate()
                    AuditService.log_async(
                        "account_pulse_anthropic_cooldown_cleared",
                        details={"account_id": account.id, "quota_key": cooldown.quota_key},
                    )
        except Exception:
            logger.warning(
                "Account pulse Anthropic cooldown reconciliation failed account_id=%s",
                account.id,
                exc_info=True,
            )

    async def _maybe_probe_fable_access(self, account: Account, access_token: str) -> None:

        from app.core.config.settings import get_settings

        try:
            settings = get_settings()
            if not settings.anthropic_fable_over_threshold_probe_enabled:
                return
            if normalize_provider_name(account.provider) != ANTHROPIC_PROVIDER_NAME:
                return
            if not _is_fable_probe_routable(account):
                return
            weekly = await self.weekly_usage_lookup(account.id)
            if weekly is None:
                return
            used_percent, weekly_reset_at = weekly
            if used_percent < settings.anthropic_fable_weekly_max_used_percent:
                return
            status, message = await self.fable_probe_sender(account, access_token)
            verdict = _classify_fable_probe(status, message)
            if verdict is FableProbeVerdict.INCONCLUSIVE:
                return
            now_epoch = naive_utc_to_epoch(to_utc_naive(self.now()))
            if verdict is FableProbeVerdict.CAPABLE:
                ttl = max(1, int(settings.anthropic_fable_probe_ttl_seconds))
                await self.fable_marker_writer(account.id, 0.0, now_epoch + ttl)
            else:
                reset_at = weekly_reset_at if weekly_reset_at is not None else now_epoch + 86400
                await self.fable_marker_writer(account.id, 100.0, reset_at)
        except Exception:
            logger.warning(
                "Account pulse Fable probe failed account_id=%s",
                account.id,
                exc_info=True,
            )

    def _in_backoff(
        self, account_id: str, *, max_delay_seconds: float | None = None, min_delay_seconds: float = 0.0
    ) -> bool:
        failure = self._failures.get(account_id)
        if failure is None:
            return False
        retry_after = max(failure.retry_after_monotonic, failure.recorded_monotonic + min_delay_seconds)
        if max_delay_seconds is not None:
            retry_after = min(retry_after, failure.recorded_monotonic + max_delay_seconds)
        return retry_after > time.monotonic()

    def _record_failure(self, account_id: str) -> None:
        previous = self._failures.get(account_id)
        attempts = 1 if previous is None else previous.attempts + 1
        base = max(0.0, float(self.failure_backoff_base_seconds))
        cap = max(base, float(self.failure_backoff_max_seconds))
        delay = min(cap, base * (2 ** min(attempts - 1, 6)))
        delay += _jitter_delay(self.jitter_seconds)
        recorded = time.monotonic()
        self._failures[account_id] = _FailureBackoff(
            attempts=attempts,
            recorded_monotonic=recorded,
            retry_after_monotonic=recorded + delay,
        )


def build_account_pulse_scheduler() -> AccountPulseScheduler:
    from app.core.config.settings import get_settings

    settings = get_settings()
    multi_replica = len(settings.http_responses_session_bridge_instance_ring) > 1
    return AccountPulseScheduler(
        interval_seconds=settings.account_pulse_interval_seconds,
        recovery_interval_seconds=settings.account_pulse_recovery_interval_seconds,
        enabled=settings.account_pulse_enabled and (settings.leader_election_enabled or not multi_replica),
        concurrency=settings.account_pulse_concurrency,
        jitter_seconds=settings.account_pulse_jitter_seconds,
        failure_backoff_base_seconds=settings.account_pulse_failure_backoff_base_seconds,
        failure_backoff_max_seconds=settings.account_pulse_failure_backoff_max_seconds,
    )


def _is_recovery_pending(account: Account) -> bool:
    if account.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
        return True
    return normalize_subscription_status(account.subscription_status) == CANCELED_SUBSCRIPTION_STATUS


def _jitter_delay(max_seconds: float) -> float:
    if max_seconds <= 0:
        return 0.0
    return random.uniform(0.0, max_seconds)
