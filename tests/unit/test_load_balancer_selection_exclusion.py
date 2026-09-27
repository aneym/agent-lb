from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, cast

import pytest

import app.modules.proxy.load_balancer as load_balancer_module
from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository

pytestmark = pytest.mark.unit

_LOCAL_INSTANCE_ID = "studio"
_OTHER_INSTANCE_ID = "laptop"


def _jwt_with_exp(exp_epoch_seconds: float) -> str:
    """An unsigned JWT-shaped string carrying only an `exp` claim — enough for
    token_expiry_epoch_ms (which just base64-decodes the payload segment)."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp_epoch_seconds}).encode()).decode().rstrip("=")
    return f"header.{payload}.sig"


def _make_account(
    account_id: str,
    *,
    owner_instance: str | None = None,
    access_token: str = "access-token",
) -> Account:
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt(access_token),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.owner_instance = owner_instance
    return account


class _StubAccountsRepository:
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = accounts

    async def reload_by_id(self, account_id: str) -> Account | None:
        return next((account for account in self._accounts if account.id == account_id), None)

    async def list_accounts(self) -> list[Account]:
        return list(self._accounts)

    async def update_status(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        reason: str | None = None,
        reset_at: int | None = None,
        *,
        blocked_at: int | None = None,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None = None,
    ) -> bool:
        account = await self.reload_by_id(account_id)
        if account is None or (
            account.status,
            account.deactivation_reason,
            account.reset_at,
            account.blocked_at,
        ) != (expected_status, expected_deactivation_reason, expected_reset_at, expected_blocked_at):
            return False
        account.status = status
        account.deactivation_reason = reason
        account.reset_at = reset_at
        account.blocked_at = blocked_at
        return True


class _StubUsageRepository:
    def __init__(self, primary: dict[str, UsageHistory], secondary: dict[str, UsageHistory]) -> None:
        self._primary = primary
        self._secondary = secondary

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        del account_ids
        if window == "secondary":
            return self._secondary
        return self._primary


class _StubStickySessionsRepository:
    async def get_account_id(self, *args: Any, **kwargs: Any) -> str | None:
        del args, kwargs
        return None

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def delete(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True


@asynccontextmanager
async def _repo_factory(accounts: list[Account]) -> AsyncIterator[ProxyRepositories]:
    yield ProxyRepositories(
        accounts=cast(Any, _StubAccountsRepository(accounts)),
        usage=cast(Any, _StubUsageRepository({}, {})),
        request_logs=cast(RequestLogsRepository, object()),
        sticky_sessions=cast(Any, _StubStickySessionsRepository()),
        api_keys=cast(ApiKeysRepository, object()),
        additional_usage=cast(AdditionalUsageRepository, object()),
    )


def _patch_local_instance_id(monkeypatch: pytest.MonkeyPatch, instance_id: str) -> None:
    settings = Settings(local_instance_id=instance_id)
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_selection_excludes_non_owned_hard_expired_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    expired_mirror = _make_account(
        "expired-mirror",
        owner_instance=_OTHER_INSTANCE_ID,
        access_token=_jwt_with_exp(time.time() - 10),
    )
    balancer = LoadBalancer(lambda: _repo_factory([expired_mirror]))

    result = await balancer.select_account()

    assert result.account is None


@pytest.mark.asyncio
async def test_selection_keeps_fresh_mirror_and_owned_expired_account(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    # A mirror must have more than five minutes of access lifetime left.
    fresh_mirror = _make_account(
        "fresh-mirror",
        owner_instance=_OTHER_INSTANCE_ID,
        access_token=_jwt_with_exp(time.time() + 360),
    )
    # Hard-expired but locally owned — the owner refreshes it on demand elsewhere;
    # selection must not exclude owned accounts regardless of expiry.
    owned_expired = _make_account(
        "owned-expired",
        owner_instance=None,
        access_token=_jwt_with_exp(time.time() - 10),
    )
    balancer = LoadBalancer(lambda: _repo_factory([fresh_mirror, owned_expired]))

    fresh_mirror_result = await balancer.select_account(account_ids=[fresh_mirror.id])
    owned_expired_result = await balancer.select_account(account_ids=[owned_expired.id])

    assert fresh_mirror_result.account is not None
    assert fresh_mirror_result.account.id == fresh_mirror.id
    assert owned_expired_result.account is not None
    assert owned_expired_result.account.id == owned_expired.id

    fresh_mirror.access_expires_at = datetime.fromtimestamp(time.time() + 240, tz=timezone.utc).replace(tzinfo=None)
    balancer = LoadBalancer(lambda: _repo_factory([fresh_mirror]))
    assert (await balancer.select_account()).account is None


@pytest.mark.asyncio
async def test_selection_keeps_uncertain_account_outside_serving_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    uncertain = _make_account("uncertain", access_token=_jwt_with_exp(time.time() + 360))
    uncertain.status = AccountStatus.EXCHANGE_UNCERTAIN
    uncertain.deactivation_reason = "Refresh exchange outcome uncertain"
    balancer = LoadBalancer(lambda: _repo_factory([uncertain]))
    assert (await balancer.select_account()).account.id == uncertain.id
    assert uncertain.status == AccountStatus.EXCHANGE_UNCERTAIN
    assert uncertain.deactivation_reason == "Refresh exchange outcome uncertain"

    uncertain.access_expires_at = datetime.fromtimestamp(time.time() + 240, tz=timezone.utc).replace(tzinfo=None)
    balancer = LoadBalancer(lambda: _repo_factory([uncertain]))
    assert (await balancer.select_account()).account is None


@pytest.mark.asyncio
async def test_excluded_mirror_absent_from_candidate_set_even_when_scoped_by_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_local_instance_id(monkeypatch, _LOCAL_INSTANCE_ID)
    expired_mirror = _make_account(
        "expired-mirror-scoped",
        owner_instance=_OTHER_INSTANCE_ID,
        access_token=_jwt_with_exp(time.time() - 10),
    )
    owned = _make_account("owned-account", owner_instance=None)
    balancer = LoadBalancer(lambda: _repo_factory([expired_mirror, owned]))

    # Scoping account_ids to exactly the excluded mirror must still yield no
    # selection — the exclusion happens upstream of the account_ids filter, so
    # the excluded account is never part of the selection loop's candidate set.
    result = await balancer.select_account(account_ids=[expired_mirror.id])

    assert result.account is None
