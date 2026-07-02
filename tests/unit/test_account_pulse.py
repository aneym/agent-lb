from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast

import pytest

from app.core.auth.refresh import RefreshError
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts import probes
from app.modules.accounts import pulse as pulse_module
from app.modules.accounts.probes import ProbeVerdict, classify_probe_result
from app.modules.accounts.pulse import AccountPulseScheduler

pytestmark = pytest.mark.unit

_OAUTH_REFUSED_MESSAGE = "OAuth authentication is currently not allowed for this organization."


def _account(
    account_id: str = "acc_1",
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    provider: str = "anthropic",
    subscription_status: str | None = None,
) -> Account:
    account = Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        alias=None,
        plan_type="claude",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime(2026, 1, 1, 12, 0, 0),
        status=status,
        deactivation_reason=None,
    )
    account.provider = provider
    account.subscription_status = subscription_status
    return account


class _Repo:
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = {account.id: account for account in accounts}
        self.status_updates: list[tuple[str, AccountStatus, str | None]] = []
        self.ledger_updates: list[dict[str, Any]] = []

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        del refresh_existing
        return list(self._accounts.values())

    async def get_by_id(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
    ) -> bool:
        del reset_at
        self.status_updates.append((account_id, status, deactivation_reason))
        return True

    async def update_subscription_ledger(self, account_id: str, **kwargs: Any) -> bool:
        self.ledger_updates.append({"account_id": account_id, **kwargs})
        return True


class _Leader:
    async def try_acquire(self) -> bool:
        return True


class _AuthManager:
    def __init__(self, failures: dict[str, RefreshError] | None = None) -> None:
        self._failures = failures or {}

    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        del force
        failure = self._failures.get(account.id)
        if failure is not None:
            raise failure
        return account


class _Encryptor:
    def decrypt(self, value: bytes) -> str:
        return value.decode()


class _SelectionCache:
    def __init__(self) -> None:
        self.invalidate_calls = 0

    def invalidate(self) -> None:
        self.invalidate_calls += 1


def _build_scheduler(
    repo: _Repo,
    *,
    probe_results: dict[str, tuple[int, str | None]],
    auth_failures: dict[str, RefreshError] | None = None,
    probe_calls: list[str] | None = None,
    interval_seconds: int = 3600,
    recovery_interval_seconds: int = 900,
    failure_backoff_base_seconds: float = 600.0,
) -> AccountPulseScheduler:
    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    async def probe_sender(account: Account, access_token: str) -> tuple[int, str | None]:
        del access_token
        if probe_calls is not None:
            probe_calls.append(account.id)
        return probe_results[account.id]

    return AccountPulseScheduler(
        interval_seconds=interval_seconds,
        enabled=True,
        concurrency=2,
        jitter_seconds=0.0,
        recovery_interval_seconds=recovery_interval_seconds,
        failure_backoff_base_seconds=failure_backoff_base_seconds,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,  # type: ignore[arg-type]
        auth_manager_factory=lambda _repo: _AuthManager(auth_failures),
        probe_sender=probe_sender,
        _encryptor=cast(TokenEncryptor, _Encryptor()),
    )


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch: pytest.MonkeyPatch) -> _SelectionCache:
    cache = _SelectionCache()
    monkeypatch.setattr(pulse_module, "get_account_selection_cache", lambda: cache)
    monkeypatch.setattr(
        pulse_module.AuditService,
        "log_async",
        staticmethod(lambda *args, **kwargs: None),
    )
    return cache


def test_classify_probe_result_covers_all_verdicts() -> None:
    assert classify_probe_result(200, None) is ProbeVerdict.HEALTHY
    assert classify_probe_result(204, None) is ProbeVerdict.HEALTHY
    assert classify_probe_result(401, None) is ProbeVerdict.DISCONNECTED
    assert classify_probe_result(403, _OAUTH_REFUSED_MESSAGE) is ProbeVerdict.UNSUBSCRIBED
    assert classify_probe_result(403, "some other forbidden reason") is ProbeVerdict.INCONCLUSIVE
    assert classify_probe_result(403, None) is ProbeVerdict.INCONCLUSIVE
    assert classify_probe_result(400, "Unsupported parameter") is ProbeVerdict.INCONCLUSIVE
    assert classify_probe_result(429, None) is ProbeVerdict.INCONCLUSIVE
    assert classify_probe_result(500, None) is ProbeVerdict.INCONCLUSIVE
    assert classify_probe_result(probes.PROBE_NETWORK_FAILURE_STATUS, "timeout") is ProbeVerdict.INCONCLUSIVE


@pytest.mark.asyncio
async def test_pulse_restores_canceled_ledger_on_healthy_probe() -> None:
    account = _account(subscription_status="canceled")
    repo = _Repo([account])
    scheduler = _build_scheduler(repo, probe_results={account.id: (200, None)})

    await scheduler.pulse_once()

    assert len(repo.ledger_updates) == 1
    assert repo.ledger_updates[0]["status"] == "active"
    assert repo.status_updates == []


@pytest.mark.asyncio
async def test_pulse_marks_ledger_canceled_on_subscription_refusal() -> None:
    account = _account(subscription_status="active")
    repo = _Repo([account])
    scheduler = _build_scheduler(repo, probe_results={account.id: (403, _OAUTH_REFUSED_MESSAGE)})

    await scheduler.pulse_once()

    assert len(repo.ledger_updates) == 1
    assert repo.ledger_updates[0]["status"] == "canceled"
    assert _OAUTH_REFUSED_MESSAGE in repo.ledger_updates[0]["notes"]
    # Credentials are intact: account status must stay untouched.
    assert repo.status_updates == []


@pytest.mark.asyncio
async def test_pulse_marks_reauth_required_on_credential_rejection() -> None:
    account = _account()
    repo = _Repo([account])
    scheduler = _build_scheduler(repo, probe_results={account.id: (401, None)})

    await scheduler.pulse_once()

    assert repo.status_updates == [
        (account.id, AccountStatus.REAUTH_REQUIRED, "Account pulse: authentication rejected (HTTP 401)")
    ]
    assert repo.ledger_updates == []


@pytest.mark.asyncio
async def test_pulse_corrects_stale_auth_failure_to_unsubscribed() -> None:
    account = _account(status=AccountStatus.DEACTIVATED, subscription_status="active")
    repo = _Repo([account])
    scheduler = _build_scheduler(repo, probe_results={account.id: (403, _OAUTH_REFUSED_MESSAGE)})

    await scheduler.pulse_once()

    # Probe authenticated → the deactivated status is stale; ledger goes
    # canceled so the account stays out of the routable pool.
    assert repo.status_updates == [(account.id, AccountStatus.ACTIVE, None)]
    assert len(repo.ledger_updates) == 1
    assert repo.ledger_updates[0]["status"] == "canceled"


@pytest.mark.asyncio
async def test_pulse_reactivates_recovered_account() -> None:
    account = _account(status=AccountStatus.DEACTIVATED)
    repo = _Repo([account])
    scheduler = _build_scheduler(repo, probe_results={account.id: (200, None)})

    await scheduler.pulse_once()

    assert repo.status_updates == [(account.id, AccountStatus.ACTIVE, None)]


@pytest.mark.asyncio
async def test_pulse_skips_paused_accounts() -> None:
    account = _account(status=AccountStatus.PAUSED)
    repo = _Repo([account])
    probe_calls: list[str] = []
    scheduler = _build_scheduler(repo, probe_results={}, probe_calls=probe_calls)

    await scheduler.pulse_once()

    assert probe_calls == []
    assert repo.status_updates == []
    assert repo.ledger_updates == []


@pytest.mark.asyncio
async def test_pulse_inconclusive_probe_makes_no_writes_and_backs_off() -> None:
    account = _account()
    repo = _Repo([account])
    probe_calls: list[str] = []
    scheduler = _build_scheduler(
        repo,
        probe_results={account.id: (400, "Unsupported parameter")},
        probe_calls=probe_calls,
    )

    await scheduler.pulse_once()
    await scheduler.pulse_once()

    assert repo.status_updates == []
    assert repo.ledger_updates == []
    # Second pass is skipped by the failure backoff.
    assert probe_calls == [account.id]


@pytest.mark.asyncio
async def test_recovery_pass_restores_canceled_ledger_on_healthy_probe() -> None:
    account = _account(subscription_status="canceled")
    repo = _Repo([account])
    scheduler = _build_scheduler(repo, probe_results={account.id: (200, None)})

    await scheduler.recovery_pulse_once()

    assert len(repo.ledger_updates) == 1
    assert repo.ledger_updates[0]["status"] == "active"
    assert repo.status_updates == []


@pytest.mark.asyncio
async def test_recovery_pass_reactivates_reauth_required_account() -> None:
    account = _account(status=AccountStatus.REAUTH_REQUIRED)
    repo = _Repo([account])
    scheduler = _build_scheduler(repo, probe_results={account.id: (200, None)})

    await scheduler.recovery_pulse_once()

    assert repo.status_updates == [(account.id, AccountStatus.ACTIVE, None)]


@pytest.mark.asyncio
async def test_recovery_pass_skips_healthy_accounts() -> None:
    healthy = _account("acc_ok", subscription_status="active")
    pending = _account("acc_pending", subscription_status="canceled")
    repo = _Repo([healthy, pending])
    probe_calls: list[str] = []
    scheduler = _build_scheduler(
        repo,
        probe_results={pending.id: (200, None)},
        probe_calls=probe_calls,
    )

    await scheduler.recovery_pulse_once()

    assert probe_calls == [pending.id]


@pytest.mark.asyncio
async def test_recovery_pass_skips_paused_accounts() -> None:
    account = _account(status=AccountStatus.PAUSED, subscription_status="canceled")
    repo = _Repo([account])
    probe_calls: list[str] = []
    scheduler = _build_scheduler(repo, probe_results={}, probe_calls=probe_calls)

    await scheduler.recovery_pulse_once()

    assert probe_calls == []
    assert repo.status_updates == []
    assert repo.ledger_updates == []


@pytest.mark.asyncio
async def test_recovery_pass_backoff_is_capped_at_recovery_interval() -> None:
    account = _account(subscription_status="canceled")
    repo = _Repo([account])
    probe_calls: list[str] = []
    scheduler = _build_scheduler(
        repo,
        probe_results={account.id: (500, None)},
        probe_calls=probe_calls,
        failure_backoff_base_seconds=7200.0,
    )

    await scheduler.recovery_pulse_once()
    # An immediate re-pass is skipped: a still-failing account is probed at
    # most once per recovery interval.
    await scheduler.recovery_pulse_once()
    assert probe_calls == [account.id]

    # Simulate the recovery interval elapsing. The raw backoff (7200s base)
    # would block for hours; the recovery lane caps it at its own cadence.
    failure = scheduler._failures[account.id]
    failure.recorded_monotonic -= scheduler.recovery_interval_seconds + 1
    assert failure.retry_after_monotonic > time.monotonic()

    await scheduler.recovery_pulse_once()
    assert probe_calls == [account.id, account.id]


@pytest.mark.asyncio
async def test_run_loop_probes_recovery_pending_accounts_on_fast_cadence() -> None:
    healthy = _account("acc_ok", subscription_status="active")
    pending = _account("acc_pending", subscription_status="canceled")
    repo = _Repo([healthy, pending])
    probe_calls: list[str] = []
    scheduler = _build_scheduler(
        repo,
        probe_results={
            healthy.id: (200, None),
            # Still refused upstream: the account stays recovery-pending.
            pending.id: (403, _OAUTH_REFUSED_MESSAGE),
        },
        probe_calls=probe_calls,
        interval_seconds=3600,
        recovery_interval_seconds=1,
    )

    await scheduler.start()
    try:
        # Poll instead of a fixed sleep so a loaded runner cannot flake the
        # cadence assertion; the loop normally satisfies this within ~2s.
        deadline = time.monotonic() + 10.0
        while probe_calls.count(pending.id) < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
    finally:
        await scheduler.stop()

    # The first wake is a full pass; every later wake within the full
    # interval is a recovery pass that only re-probes the canceled account.
    assert probe_calls.count(healthy.id) == 1
    assert probe_calls.count(pending.id) >= 2


@pytest.mark.asyncio
async def test_pulse_permanent_refresh_failure_marks_reauth_required() -> None:
    account = _account()
    repo = _Repo([account])
    error = RefreshError("invalid_grant", "refresh token revoked", True)
    scheduler = _build_scheduler(
        repo,
        probe_results={account.id: (200, None)},
        auth_failures={account.id: error},
    )

    await scheduler.pulse_once()

    assert len(repo.status_updates) == 1
    assert repo.status_updates[0][1] is AccountStatus.REAUTH_REQUIRED
    assert "token refresh failed" in (repo.status_updates[0][2] or "")


@pytest.mark.asyncio
async def test_pulse_transient_refresh_failure_makes_no_writes() -> None:
    account = _account()
    repo = _Repo([account])
    error = RefreshError("network_error", "connect timeout", False, transport_error=True)
    scheduler = _build_scheduler(
        repo,
        probe_results={account.id: (200, None)},
        auth_failures={account.id: error},
    )

    await scheduler.pulse_once()

    assert repo.status_updates == []
    assert repo.ledger_updates == []
