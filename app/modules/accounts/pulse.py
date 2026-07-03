from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, cast

from app.core.audit.service import AuditService
from app.core.auth.refresh import RefreshError, classify_refresh_error
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME, GLM_PROVIDER_NAME, normalize_provider_name
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts import probes
from app.modules.accounts.auth_manager import AuthManager, is_locally_owned
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.subscription_status import (
    ACTIVE_SUBSCRIPTION_STATUS,
    CANCELED_SUBSCRIPTION_STATUS,
    is_subscription_usable,
    normalize_subscription_status,
)
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

logger = logging.getLogger(__name__)

_FABLE_PROBE_MODEL = "claude-fable-5"
# Mirrored in anthropic_service.py's _provider_quota_eligibility read side —
# both must agree on the quota_key/window identifying this marker.
ANTHROPIC_FABLE_ACCESS_QUOTA_KEY = "anthropic_fable_access"
_ANTHROPIC_FABLE_PROBE_FEATURE = "anthropic_fable_probe"
_ANTHROPIC_FABLE_PROBE_WINDOW = "primary"


class FableProbeVerdict(str, Enum):
    CAPABLE = "capable"
    REFUSED = "refused"
    INCONCLUSIVE = "inconclusive"


def _classify_fable_probe(status: int, message: str | None) -> FableProbeVerdict:
    """Map a claude-fable-5 probe HTTP status to a Fable-capability verdict.

    Unlike the general account-health probe, a 4xx here is a real signal:
    the probe body is fixed and known-good, so a model/permission refusal
    (400/403/404) means Anthropic rejected Fable specifically for this
    account. 429/5xx/network failures stay inconclusive and must not write
    a marker — the account keeps its prior state until a decisive probe.
    """
    del message  # reserved for future refinement of refusal detection
    if 200 <= status < 300:
        return FableProbeVerdict.CAPABLE
    if status in (400, 403, 404):
        return FableProbeVerdict.REFUSED
    return FableProbeVerdict.INCONCLUSIVE


def _is_fable_probe_routable(account: Account) -> bool:
    """Same routable notion selection uses (see load_balancer.selectable_accounts):

    excludes reauth-required, deactivated, and paused rows, and canceled
    subscriptions — those can never serve Fable traffic regardless of a
    probe outcome, so probing them is pointless until the main pulse probe
    (not this one) restores them.
    """
    return (
        account.status not in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED)
        and is_subscription_usable(account)
    )


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


class _AccountsRepositoryLike(Protocol):
    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]: ...

    async def get_by_id(self, account_id: str) -> Account | None: ...

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
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


_RepoFactory = Callable[[], AbstractAsyncContextManager[_AccountsRepositoryLike]]
_AuthManagerFactory = Callable[[_AccountsRepositoryLike], _AuthManagerLike]
_LeaderElectionFactory = Callable[[], _LeaderElectionLike]
_ProbeSender = Callable[[Account, str], Awaitable[tuple[int, str | None]]]
# (used_percent, reset_at) of the account's latest weekly (secondary) usage
# entry, or None when no such entry exists.
_WeeklyUsageLookup = Callable[[str], Awaitable[tuple[float, int | None] | None]]
_FableMarkerWriter = Callable[[str, float, int | None], Awaitable[None]]


@dataclass(slots=True)
class _FailureBackoff:
    attempts: int
    recorded_monotonic: float
    retry_after_monotonic: float


@dataclass(slots=True)
class AccountPulseScheduler:
    """Periodically verifies every non-paused account with a real upstream probe.

    Classification is deliberately conservative (see ``classify_probe_result``):
    only a 2xx, a 401, or a 403 carrying a known subscription-refusal marker
    changes account state. The pulse keeps three dashboard-visible states
    truthful without operator action:

    - authenticated + subscribed (``active`` with a usable subscription),
    - authenticated but unsubscribed (subscription ledger ``canceled``,
      credentials intact),
    - disconnected (``reauth_required`` — credentials rejected upstream).

    Paused accounts are operator intent and are never probed.

    Between full passes a fast recovery lane re-probes only recovery-pending
    accounts (subscription ledger ``canceled`` or status
    ``reauth_required``/``deactivated``) every ``recovery_interval_seconds``,
    so an account fixed upstream re-enters the routable pool within minutes
    instead of waiting for the next full pass.
    """

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
    fable_probe_sender: _ProbeSender = field(default_factory=lambda: _default_fable_probe_sender)
    weekly_usage_lookup: _WeeklyUsageLookup = field(default_factory=lambda: _default_weekly_usage_lookup)
    fable_marker_writer: _FableMarkerWriter = field(default_factory=lambda: _default_fable_marker_writer)
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
        # Wakes happen at the recovery cadence; each wake runs the fast
        # recovery lane unless a full interval has elapsed since the last
        # full pass. With recovery_interval_seconds >= interval_seconds the
        # loop degrades to the original single-cadence behavior.
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
            # Candidate filtering must happen inside the repo session: the ORM
            # instances detach once the session closes and attribute access
            # raises DetachedInstanceError. Only ids may escape this block.
            async with self.repo_factory() as repo:
                accounts = await repo.list_accounts(refresh_existing=True)
                candidate_ids = [
                    account.id
                    for account in accounts
                    if self._is_pulse_candidate(account, recovery_only=recovery_only)
                ]
            if not candidate_ids:
                return
            semaphore = asyncio.Semaphore(max(1, self.concurrency))
            # return_exceptions keeps sibling probes awaited (never orphaned
            # past the lock) when one candidate raises unexpectedly.
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
        if not recovery_only:
            return not self._in_backoff(account.id)
        if not _is_recovery_pending(account):
            return False
        # The recovery lane caps the effective failure backoff at its own
        # cadence: a transient failure on a recovery-pending account must
        # never push recovery detection past the full interval, while the
        # wake cadence still bounds probes to one per recovery interval.
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
                        await self._mark_disconnected(
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
                await self._apply_verdict(repo, fresh_account, verdict, status, message)
                # Runs after the normal verdict is fully applied, and is
                # internally exception-safe, so a Fable-probe failure can
                # never suppress or alter the handling above.
                await self._maybe_probe_fable_access(fresh_account, access_token)

    async def _maybe_probe_fable_access(self, account: Account, access_token: str) -> None:
        """Send an additional tiny claude-fable-5 probe to routable Anthropic
        accounts at/over the Fable weekly threshold and record the outcome as
        a Fable-access marker (see ANTHROPIC_FABLE_ACCESS_QUOTA_KEY).

        This never changes account status, the subscription ledger, or any
        other routing state — it only feeds the marker that
        ``_provider_quota_eligibility`` reads to decide Fable eligibility.
        """
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

    async def _apply_verdict(
        self,
        repo: _AccountsRepositoryLike,
        account: Account,
        verdict: probes.ProbeVerdict,
        status: int,
        message: str | None,
    ) -> None:
        checked_at = to_utc_naive(self.now())
        date_label = checked_at.date().isoformat()
        subscription_canceled = (
            normalize_subscription_status(account.subscription_status) == CANCELED_SUBSCRIPTION_STATUS
        )
        if verdict is probes.ProbeVerdict.HEALTHY:
            self._failures.pop(account.id, None)
            if subscription_canceled:
                await repo.update_subscription_ledger(
                    account.id,
                    status=ACTIVE_SUBSCRIPTION_STATUS,
                    next_charge_at=account.subscription_next_charge_at,
                    current_period_end_at=account.subscription_current_period_end_at,
                    amount=account.subscription_amount,
                    currency=account.subscription_currency,
                    last_verified_at=checked_at,
                    notes=f"Account pulse verified the subscription is usable on {date_label}.",
                )
                get_account_selection_cache().invalidate()
                AuditService.log_async(
                    "account_pulse_subscription_restored",
                    details={"account_id": account.id, "probe_status_code": status},
                )
                logger.info("Account pulse restored subscription account_id=%s", account.id)
            if account.status in (AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED):
                await repo.update_status(account.id, AccountStatus.ACTIVE, None)
                get_account_selection_cache().invalidate()
                AuditService.log_async(
                    "account_pulse_reactivated",
                    details={
                        "account_id": account.id,
                        "previous_status": account.status.value,
                        "probe_status_code": status,
                    },
                )
                logger.info("Account pulse reactivated account_id=%s", account.id)
            return
        if verdict is probes.ProbeVerdict.UNSUBSCRIBED:
            self._failures.pop(account.id, None)
            if account.status in (AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED):
                # The probe authenticated, so a stored auth-failure status is
                # stale — the account is unsubscribed, not disconnected. The
                # canceled ledger keeps it out of the routable pool.
                await repo.update_status(account.id, AccountStatus.ACTIVE, None)
                get_account_selection_cache().invalidate()
                AuditService.log_async(
                    "account_pulse_reactivated",
                    details={
                        "account_id": account.id,
                        "previous_status": account.status.value,
                        "probe_status_code": status,
                    },
                )
                logger.info(
                    "Account pulse cleared stale auth-failure status for unsubscribed account_id=%s",
                    account.id,
                )
            if not subscription_canceled:
                await repo.update_subscription_ledger(
                    account.id,
                    status=CANCELED_SUBSCRIPTION_STATUS,
                    next_charge_at=account.subscription_next_charge_at,
                    current_period_end_at=account.subscription_current_period_end_at,
                    amount=account.subscription_amount,
                    currency=account.subscription_currency,
                    last_verified_at=checked_at,
                    notes=(
                        f"Account pulse detected a subscription refusal (HTTP {status}) on {date_label}: "
                        f"{message or 'no upstream message'}"
                    ),
                )
                get_account_selection_cache().invalidate()
                AuditService.log_async(
                    "account_pulse_subscription_canceled",
                    details={
                        "account_id": account.id,
                        "probe_status_code": status,
                        "message": (message or "")[:200],
                    },
                )
                logger.warning(
                    "Account pulse marked subscription canceled account_id=%s status=%s",
                    account.id,
                    status,
                )
            return
        if verdict is probes.ProbeVerdict.DISCONNECTED:
            self._failures.pop(account.id, None)
            if account.status == AccountStatus.ACTIVE:
                await self._mark_disconnected(
                    repo,
                    account,
                    reason=f"Account pulse: authentication rejected (HTTP {status})",
                )
            return
        self._record_failure(account.id)
        logger.info(
            "Account pulse inconclusive account_id=%s status=%s",
            account.id,
            status,
        )

    async def _mark_disconnected(
        self,
        repo: _AccountsRepositoryLike,
        account: Account,
        *,
        reason: str,
    ) -> None:
        await repo.update_status(account.id, AccountStatus.REAUTH_REQUIRED, reason)
        get_account_selection_cache().invalidate()
        AuditService.log_async(
            "account_pulse_reauth_required",
            details={"account_id": account.id, "reason": reason},
        )
        logger.warning("Account pulse marked reauth required account_id=%s reason=%s", account.id, reason)

    def _in_backoff(self, account_id: str, *, max_delay_seconds: float | None = None) -> bool:
        failure = self._failures.get(account_id)
        if failure is None:
            return False
        retry_after = failure.retry_after_monotonic
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


async def _default_probe_sender(account: Account, access_token: str) -> tuple[int, str | None]:
    from app.core.config.settings import get_settings

    settings = get_settings()
    provider = normalize_provider_name(account.provider)
    if provider == ANTHROPIC_PROVIDER_NAME:
        return await probes.send_messages_probe(
            access_token=access_token,
            base_url=settings.anthropic_upstream_base_url,
            model=probes.DEFAULT_ANTHROPIC_SUBSCRIPTION_CHECK_MODEL,
        )
    if provider == GLM_PROVIDER_NAME:
        return await probes.send_messages_probe(
            access_token=access_token,
            base_url=settings.glm_anthropic_upstream_base_url,
            model=probes.DEFAULT_GLM_PROBE_MODEL,
        )
    status = await probes.send_openai_probe(
        access_token=access_token,
        chatgpt_account_id=account.chatgpt_account_id,
        model=probes.DEFAULT_PROBE_MODEL,
    )
    return status, None


async def _default_fable_probe_sender(account: Account, access_token: str) -> tuple[int, str | None]:
    # Reuses the exact probe machinery POST /api/accounts/{id}/probe and the
    # main pulse probe use (probes.send_messages_probe, max_tokens=4), just
    # pinned to the Fable model instead of the subscription-check model.
    del account
    from app.core.config.settings import get_settings

    settings = get_settings()
    return await probes.send_messages_probe(
        access_token=access_token,
        base_url=settings.anthropic_upstream_base_url,
        model=_FABLE_PROBE_MODEL,
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


def _is_recovery_pending(account: Account) -> bool:
    if account.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
        return True
    return normalize_subscription_status(account.subscription_status) == CANCELED_SUBSCRIPTION_STATUS


def _jitter_delay(max_seconds: float) -> float:
    if max_seconds <= 0:
        return 0.0
    return random.uniform(0.0, max_seconds)
