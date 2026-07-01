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
from typing import Protocol, cast

from app.core.audit.service import AuditService
from app.core.auth.refresh import RefreshError, classify_refresh_error
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME, GLM_PROVIDER_NAME, normalize_provider_name
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts import probes
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.subscription_status import (
    ACTIVE_SUBSCRIPTION_STATUS,
    CANCELED_SUBSCRIPTION_STATUS,
    normalize_subscription_status,
)
from app.modules.proxy.account_cache import get_account_selection_cache

logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class _FailureBackoff:
    attempts: int
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
    """

    interval_seconds: int
    enabled: bool
    concurrency: int
    jitter_seconds: float
    failure_backoff_base_seconds: float = 600.0
    failure_backoff_max_seconds: float = 21600.0
    leader_election_factory: _LeaderElectionFactory = field(default_factory=lambda: _get_leader_election)
    repo_factory: _RepoFactory = field(default_factory=lambda: _default_accounts_repo_factory)
    auth_manager_factory: _AuthManagerFactory = field(default_factory=lambda: _default_auth_manager_factory)
    probe_sender: _ProbeSender = field(default_factory=lambda: _default_probe_sender)
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
        while not self._stop.is_set():
            jitter = _jitter_delay(self.jitter_seconds)
            if jitter > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=jitter)
                    break
                except asyncio.TimeoutError:
                    pass
            try:
                await self.pulse_once()
            except Exception:
                logger.exception("Account pulse pass failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def pulse_once(self) -> None:
        if not await self.leader_election_factory().try_acquire():
            return
        async with self._lock:
            async with self.repo_factory() as repo:
                accounts = await repo.list_accounts(refresh_existing=True)
            candidates = [
                account
                for account in accounts
                if account.status != AccountStatus.PAUSED and not self._in_backoff(account.id)
            ]
            if not candidates:
                return
            semaphore = asyncio.Semaphore(max(1, self.concurrency))
            await asyncio.gather(*(self._pulse_candidate(account.id, semaphore) for account in candidates))

    async def _pulse_candidate(self, account_id: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            async with self.repo_factory() as repo:
                account = await repo.get_by_id(account_id)
                if account is None or account.status == AccountStatus.PAUSED:
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

    def _in_backoff(self, account_id: str) -> bool:
        failure = self._failures.get(account_id)
        if failure is None:
            return False
        return failure.retry_after_monotonic > time.monotonic()

    def _record_failure(self, account_id: str) -> None:
        previous = self._failures.get(account_id)
        attempts = 1 if previous is None else previous.attempts + 1
        base = max(0.0, float(self.failure_backoff_base_seconds))
        cap = max(base, float(self.failure_backoff_max_seconds))
        delay = min(cap, base * (2 ** min(attempts - 1, 6)))
        delay += _jitter_delay(self.jitter_seconds)
        self._failures[account_id] = _FailureBackoff(
            attempts=attempts,
            retry_after_monotonic=time.monotonic() + delay,
        )


def build_account_pulse_scheduler() -> AccountPulseScheduler:
    from app.core.config.settings import get_settings

    settings = get_settings()
    multi_replica = len(settings.http_responses_session_bridge_instance_ring) > 1
    return AccountPulseScheduler(
        interval_seconds=settings.account_pulse_interval_seconds,
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


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@asynccontextmanager
async def _default_accounts_repo_factory() -> AsyncIterator[AccountsRepository]:
    async with get_background_session() as session:
        yield AccountsRepository(session)


def _default_auth_manager_factory(repo: _AccountsRepositoryLike) -> _AuthManagerLike:
    return AuthManager(cast(AccountsRepository, repo), refresh_repo_factory=_default_accounts_repo_factory)


def _jitter_delay(max_seconds: float) -> float:
    if max_seconds <= 0:
        return 0.0
    return random.uniform(0.0, max_seconds)
