from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.core.auth.refresh import TokenRefreshResult
from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts import pulse as pulse_module
from app.modules.accounts.auth_manager import AccountNotOwnedError, AuthManager, is_locally_owned
from app.modules.accounts.pulse import AccountPulseScheduler

pytestmark = pytest.mark.unit

_LOCAL_INSTANCE_ID = "studio"
_OTHER_INSTANCE_ID = "laptop"


def _account(
    account_id: str = "acc_1",
    *,
    owner_instance: str | None = None,
    last_refresh: datetime | None = None,
) -> Account:
    # Constructed lazily (per call, inside the test body) rather than as a
    # module-level singleton: the autouse `temp_key_file` fixture in
    # tests/conftest.py points AGENT_LB_ENCRYPTION_KEY_FILE at a fresh temp
    # key per test and clears the settings cache, so a TokenEncryptor built
    # at import time would silently use a different key than the one
    # AuthManager picks up inside the test.
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        provider="anthropic",
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        alias=None,
        plan_type="claude",
        access_token_encrypted=encryptor.encrypt("access-token"),
        refresh_token_encrypted=encryptor.encrypt("refresh-token"),
        id_token_encrypted=None,
        last_refresh=last_refresh if last_refresh is not None else utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.owner_instance = owner_instance
    return account


class _Repo:
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = {account.id: account for account in accounts}

    async def get_by_id(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    async def update_status(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def update_tokens(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def workspace_slot_taken(self, *args: Any, **kwargs: Any) -> bool:
        return False


class _FakeProvider:
    name = "anthropic"
    requires_id_token = False
    access_token_refresh_interval_seconds = None

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def refresh_access_token(self, refresh_token: str, *, session: Any = None) -> TokenRefreshResult:
        del session
        self._calls.append(refresh_token)
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token=None,
            account_id=None,
            plan_type=None,
            email=None,
        )


@pytest.fixture(autouse=True)
def _oauth_call_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    provider = _FakeProvider(calls)
    monkeypatch.setattr(auth_manager_module, "get_provider", lambda name: provider)
    return calls


def _patch_local_instance_id(monkeypatch: pytest.MonkeyPatch, instance_id: str) -> None:
    settings = Settings(local_instance_id=instance_id)
    # auth_manager imports get_settings at module load time (name bound once),
    # so it must be patched directly; pulse imports it lazily inside the
    # function body, so patching the settings module attribute is enough.
    monkeypatch.setattr(auth_manager_module, "get_settings", lambda: settings)
    import app.core.config.settings as settings_module

    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)


def _stale_last_refresh() -> datetime:
    return utcnow() - timedelta(days=30)


@pytest.mark.asyncio
async def test_owner_instance_none_refreshes_when_stale(
    monkeypatch: pytest.MonkeyPatch, _oauth_call_spy: list[str]
) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    account = _account(owner_instance=None, last_refresh=_stale_last_refresh())
    original_access_token_encrypted = account.access_token_encrypted
    manager = AuthManager(_Repo([account]))

    refreshed = await manager.ensure_fresh(account, force=False)

    assert refreshed.access_token_encrypted != original_access_token_encrypted
    assert _oauth_call_spy == ["refresh-token"]


@pytest.mark.asyncio
async def test_owner_instance_matches_local_refreshes(
    monkeypatch: pytest.MonkeyPatch, _oauth_call_spy: list[str]
) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    account = _account(owner_instance=_LOCAL_INSTANCE_ID, last_refresh=_stale_last_refresh())
    original_access_token_encrypted = account.access_token_encrypted
    manager = AuthManager(_Repo([account]))

    refreshed = await manager.ensure_fresh(account, force=False)

    assert refreshed.access_token_encrypted != original_access_token_encrypted
    assert _oauth_call_spy == ["refresh-token"]


@pytest.mark.asyncio
async def test_non_owner_fresh_token_skips_refresh_without_error(
    monkeypatch: pytest.MonkeyPatch, _oauth_call_spy: list[str]
) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    account = _account(owner_instance=_OTHER_INSTANCE_ID)  # default last_refresh is fresh (now)
    manager = AuthManager(_Repo([account]))

    returned = await manager.ensure_fresh(account, force=False)

    assert returned is account
    assert _oauth_call_spy == []


@pytest.mark.asyncio
async def test_non_owner_stale_token_raises_without_oauth_call(
    monkeypatch: pytest.MonkeyPatch, _oauth_call_spy: list[str]
) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    account = _account(owner_instance=_OTHER_INSTANCE_ID, last_refresh=_stale_last_refresh())
    manager = AuthManager(_Repo([account]))

    with pytest.raises(AccountNotOwnedError) as exc_info:
        await manager.ensure_fresh(account, force=False)

    assert exc_info.value.account_id == account.id
    assert exc_info.value.owner_instance == _OTHER_INSTANCE_ID
    assert exc_info.value.local_instance_id == _LOCAL_INSTANCE_ID
    assert _oauth_call_spy == []


@pytest.mark.asyncio
async def test_non_owner_force_true_raises_without_oauth_call(
    monkeypatch: pytest.MonkeyPatch, _oauth_call_spy: list[str]
) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    account = _account(owner_instance=_OTHER_INSTANCE_ID)  # default last_refresh is fresh (now)
    manager = AuthManager(_Repo([account]))

    with pytest.raises(AccountNotOwnedError):
        await manager.ensure_fresh(account, force=True)

    assert _oauth_call_spy == []


def test_is_locally_owned_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(local_instance_id=_LOCAL_INSTANCE_ID)
    assert is_locally_owned(_account(owner_instance=None), settings)
    assert is_locally_owned(_account(owner_instance=_LOCAL_INSTANCE_ID), settings)
    assert not is_locally_owned(_account(owner_instance=_OTHER_INSTANCE_ID), settings)


class _PulseRepo:
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = {account.id: account for account in accounts}

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
        del account_id, status, deactivation_reason, reset_at
        return True

    async def update_subscription_ledger(self, account_id: str, **kwargs: Any) -> bool:
        del account_id, kwargs
        return True


class _Leader:
    async def try_acquire(self) -> bool:
        return True


class _PulseAuthManager:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        del force
        self._calls.append(account.id)
        return account


@pytest.mark.asyncio
async def test_pulse_skips_non_owned_account_and_processes_owned_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    monkeypatch.setattr(pulse_module, "get_account_selection_cache", lambda: _NullCache())
    monkeypatch.setattr(pulse_module.AuditService, "log_async", staticmethod(lambda *a, **k: None))

    owned_account = _account("owned", owner_instance=None)
    non_owned_account = _account("non_owned", owner_instance=_OTHER_INSTANCE_ID)
    repo = _PulseRepo([owned_account, non_owned_account])
    ensure_fresh_calls: list[str] = []

    async def probe_sender(account: Account, access_token: str) -> tuple[int, str | None]:
        del access_token
        return (200, None)

    async def weekly_usage_lookup(account_id: str) -> tuple[float, int | None] | None:
        del account_id
        return None

    async def fable_probe_sender(account: Account, access_token: str) -> tuple[int, str | None]:
        del account, access_token
        return (200, None)

    async def fable_marker_writer(account_id: str, used_percent: float, reset_at: int | None) -> None:
        del account_id, used_percent, reset_at

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_PulseRepo]:
        yield repo

    scheduler = AccountPulseScheduler(
        interval_seconds=3600,
        enabled=True,
        concurrency=2,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,  # type: ignore[arg-type]
        auth_manager_factory=lambda _repo: _PulseAuthManager(ensure_fresh_calls),
        probe_sender=probe_sender,
        weekly_usage_lookup=weekly_usage_lookup,
        fable_probe_sender=fable_probe_sender,
        fable_marker_writer=fable_marker_writer,
    )

    await scheduler.pulse_once()

    assert ensure_fresh_calls == ["owned"]


class _NullCache:
    def invalidate(self) -> None:
        pass
