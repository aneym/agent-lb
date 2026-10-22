from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Protocol, TypeAlias

from app.core.auth import token_expiry_epoch_ms
from app.core.auth.refresh import (
    RefreshError,
    TokenRefreshResult,
    refresh_access_token,
    should_refresh,
)
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    KIMI_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    Provider,
    ProviderLookupError,
    get_provider,
)
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db import session as db_session
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts import refresh_lock
from app.modules.accounts.repository import EXCHANGE_RESET_REASONS
from app.modules.proxy.account_cache import get_account_selection_cache


class AccountsRepositoryPort(Protocol):
    async def get_by_id(self, account_id: str) -> Account | None: ...

    async def reload_by_id(self, account_id: str) -> Account | None: ...

    async def exchange_intent_hash(self, account_id: str) -> str | None: ...

    async def drop_stale_exchange_intent(self, account_id: str, stale_hash: str, expected_token: bytes) -> bool: ...

    async def begin_exchange(
        self,
        account_id: str,
        token_hash: str,
        expected_token: bytes,
        *,
        replay: bool = False,
        timeout_seconds: float | None = None,
    ) -> bool: ...

    async def mark_exchange_uncertain(
        self, account_id: str, token_hash: str, expected_token: bytes, *, reason: str | None = None
    ) -> None: ...

    async def abort_exchange(self, account_id: str, token_hash: str) -> None: ...

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool: ...

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes | None,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
        access_expires_at: datetime | None = None,
        exchange_token_hash: str | None = None,
    ) -> bool: ...

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool: ...


class RefreshAdmissionLeasePort(Protocol):
    def release(self) -> None: ...


logger = logging.getLogger(__name__)
_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 300

_ACCESS_TOKEN_SERVING_MARGIN_SECONDS = 300


class AccountNotOwnedError(Exception):
    def __init__(self, account_id: str, owner_instance: str | None, local_instance_id: str) -> None:
        message = (
            f"Account {account_id} is owned by instance {owner_instance!r}, not the "
            f"local instance {local_instance_id!r}; refusing to refresh"
        )
        super().__init__(message)
        self.account_id = account_id
        self.owner_instance = owner_instance
        self.local_instance_id = local_instance_id


def is_locally_owned(account: Account, settings: Settings) -> bool:
    return account.owner_instance is None or account.owner_instance == settings.local_instance_id


_RefreshSingleflightKey: TypeAlias = tuple[str, str]


class _RefreshSingleflight:
    def __init__(self) -> None:
        self._inflight: dict[_RefreshSingleflightKey, asyncio.Task[Account]] = {}
        self._recent_failures: dict[_RefreshSingleflightKey, tuple[float, tuple[str, str, bool]]] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        key: _RefreshSingleflightKey,
        factory: Callable[[], Coroutine[object, object, Account]],
    ) -> Account:
        account_id = key[0]
        async with self._lock:
            self._purge_stale_versions(account_id, keep_key=key)
            cached_failure = self._recent_failures.get(key)
            if cached_failure is not None:
                expires_at, failure = cached_failure
                if expires_at > time.monotonic():
                    code, message, is_permanent = failure
                    raise RefreshError(code, message, is_permanent)
                self._recent_failures.pop(key, None)
            task = self._inflight.get(key)
            if task is not None and task.done() and not task.cancelled() and task.exception() is None:
                pass
            elif task is None or task.done():
                task = asyncio.create_task(factory())
                self._inflight[key] = task
                task.add_done_callback(lambda done, *, cache_key=key: self._schedule_complete(cache_key, done))
        assert task is not None
        return await asyncio.shield(task)

    def _schedule_complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        asyncio.create_task(self._complete(key, task))

    async def _complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        try:
            async with self._lock:
                current = self._inflight.get(key)
                if current is task:
                    self._inflight.pop(key, None)
                if task.cancelled():
                    self._recent_failures.pop(key, None)
                    return
                try:
                    task.result()
                except RefreshError as exc:
                    ttl = max(0.0, float(get_settings().proxy_refresh_failure_cooldown_seconds))
                    if ttl > 0 and not exc.transport_error:
                        self._recent_failures[key] = (
                            time.monotonic() + ttl,
                            (exc.code, exc.message, exc.is_permanent),
                        )
                    else:
                        self._recent_failures.pop(key, None)
                except BaseException:
                    self._recent_failures.pop(key, None)
                else:
                    self._recent_failures.pop(key, None)
        except BaseException:
            logger.exception("Refresh singleflight completion cleanup failed key=%s", key)

    def _purge_stale_versions(self, account_id: str, *, keep_key: _RefreshSingleflightKey) -> None:
        stale_failures = [key for key in self._recent_failures if key[0] == account_id and key != keep_key]
        for key in stale_failures:
            self._recent_failures.pop(key, None)
        stale_inflight = [
            key for key, task in self._inflight.items() if key[0] == account_id and key != keep_key and task.done()
        ]
        for key in stale_inflight:
            self._inflight.pop(key, None)

    def clear(self) -> None:
        self._inflight.clear()
        self._recent_failures.clear()


_REFRESH_SINGLEFLIGHT = _RefreshSingleflight()


from app.modules.accounts.auth_exchange import (
    call_with_supported_optional_kwargs,
    exchange_timeout_seconds,
    refresh_account_locked,
    refresh_token_material_fingerprint,
    snapshot,
)


class AuthManager:
    def __init__(
        self,
        repo: AccountsRepositoryPort,
        *,
        acquire_refresh_admission: Callable[[], Awaitable[RefreshAdmissionLeasePort]] | None = None,
        refresh_repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepositoryPort]] | None = None,
    ) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()
        self._acquire_refresh_admission = acquire_refresh_admission

        self._refresh_repo_factory = refresh_repo_factory

    async def ensure_fresh(self, account: Account, *, force: bool = False, recover_uncertain: bool = False) -> Account:
        settings = get_settings()

        latest = await self._repo.reload_by_id(account.id)
        if latest is not None:
            account = latest

        account = snapshot(self._repo, account)
        if not is_locally_owned(account, settings):
            if force or access_token_hard_expired(self._encryptor, account):
                raise AccountNotOwnedError(account.id, account.owner_instance, settings.local_instance_id)
            return await self._ensure_chatgpt_account_id(account)
        recover_exchange = False
        if account.status == AccountStatus.EXCHANGE_UNCERTAIN:
            if recover_uncertain and isinstance(getattr(self._repo, "session", None), db_session.AsyncSession):
                intent = await self._repo.exchange_intent_window(account.id, exchange_timeout_seconds())
                recover_exchange = (
                    intent is not None
                    and intent.token_hash
                    == refresh_token_material_fingerprint(self._encryptor, account.refresh_token_encrypted)
                    and intent.ready
                    and (
                        intent.reason in EXCHANGE_RESET_REASONS
                        or (not intent.replay and intent.reason not in {"unsaved", "auto_exhausted"})
                    )
                )
            if not force and not recover_exchange and not access_token_hard_expired(self._encryptor, account):
                return await self._ensure_chatgpt_account_id(account)
        if force or recover_exchange or _account_needs_refresh(self._encryptor, account):
            account = await _REFRESH_SINGLEFLIGHT.run(
                _refresh_singleflight_key(self._encryptor, account),
                lambda: self._run_refresh(account),
            )
        return await self._ensure_chatgpt_account_id(account)

    async def _run_refresh(self, account: Account) -> Account:

        if self._refresh_repo_factory is None:
            return await self.refresh_account(account)
        async with self._refresh_repo_factory() as repo:
            owned = AuthManager(repo, acquire_refresh_admission=self._acquire_refresh_admission)
            return await owned.refresh_account(account)

    async def refresh_account(self, account: Account) -> Account:
        try:
            provider = get_provider(account.provider)
        except ProviderLookupError as exc:
            raise RefreshError("unsupported_provider", str(exc), True) from exc
        async with refresh_lock._cross_process_refresh_lock(account.id):
            return await refresh_account_locked(
                self._repo,
                self._encryptor,
                account,
                provider,
                self._refresh_tokens,
                is_locally_owned,
                AccountNotOwnedError,
                expiry_datetime,
                logger,
                lambda: get_account_selection_cache().invalidate(),
            )

    async def _refresh_tokens(
        self,
        refresh_token: str,
        *,
        account: Account,
        provider: Provider,
        on_exchange_start: Callable[[], None] | None = None,
    ) -> TokenRefreshResult:
        refresh_lease: RefreshAdmissionLeasePort | None = None
        if self._acquire_refresh_admission is not None:
            refresh_lease = await self._acquire_refresh_admission()
        try:
            if provider.name not in (OPENAI_PROVIDER_NAME, ANTHROPIC_PROVIDER_NAME, KIMI_PROVIDER_NAME):
                if on_exchange_start:
                    on_exchange_start()
                return await provider.refresh_access_token(refresh_token)
            if provider.name != OPENAI_PROVIDER_NAME:
                return await call_with_supported_optional_kwargs(
                    provider.refresh_access_token,
                    refresh_token,
                    optional_kwargs={"on_exchange_start": on_exchange_start},
                )
            async with get_background_session() as session:
                try:
                    route = await resolve_upstream_route(
                        session,
                        account_id=account.id,
                        operation="token_refresh",
                        scope="account",
                        encryptor=self._encryptor,
                    )
                except UpstreamProxyRouteError as exc:
                    raise RefreshError(
                        "upstream_proxy_unavailable",
                        f"Upstream proxy route unavailable: {exc.reason}",
                        False,
                        transport_error=True,
                        upstream_proxy_fail_closed_reason=exc.reason,
                    ) from exc
            return await call_with_supported_optional_kwargs(
                refresh_access_token,
                refresh_token,
                optional_kwargs={
                    "route": route,
                    "allow_direct_egress": route is None,
                    "on_exchange_start": on_exchange_start,
                },
            )
        finally:
            if refresh_lease is not None:
                refresh_lease.release()

    async def _ensure_chatgpt_account_id(self, account: Account) -> Account:
        if account.chatgpt_account_id:
            return account
        if account.provider != OPENAI_PROVIDER_NAME or account.id_token_encrypted is None:
            return account
        try:
            id_token = self._encryptor.decrypt(account.id_token_encrypted)
        except Exception:
            return account
        raw_account_id = get_provider(account.provider).account_metadata_from_id_token(id_token).account_id
        if not raw_account_id:
            return account

        expected_refresh_token_encrypted = account.refresh_token_encrypted
        account.chatgpt_account_id = raw_account_id
        try:
            updated = await self._repo.update_tokens(
                account.id,
                access_token_encrypted=account.access_token_encrypted,
                refresh_token_encrypted=account.refresh_token_encrypted,
                id_token_encrypted=account.id_token_encrypted,
                last_refresh=account.last_refresh,
                plan_type=account.plan_type,
                email=account.email,
                chatgpt_account_id=raw_account_id,
                workspace_id=account.workspace_id,
                workspace_label=account.workspace_label,
                seat_type=account.seat_type,
                expected_refresh_token_encrypted=expected_refresh_token_encrypted,
            )
            if not updated:
                latest = await self._repo.reload_by_id(account.id)
                if latest is not None:
                    return snapshot(self._repo, latest)
        except Exception:
            logger.warning("Failed to persist chatgpt_account_id account_id=%s", account.id, exc_info=True)
        return account


def _chatgpt_account_id_from_id_token(id_token: str) -> str | None:
    return get_provider(OPENAI_PROVIDER_NAME).account_metadata_from_id_token(id_token).account_id


def _refresh_singleflight_key(encryptor: TokenEncryptor, account: Account) -> _RefreshSingleflightKey:
    return (account.id, refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted))


def _expires_within(expires_ms: int, *, margin_seconds: float, now: datetime | None = None) -> bool:

    current = to_utc_naive(now) if now is not None else utcnow()
    threshold_ms = naive_utc_to_epoch(current) * 1000 + (margin_seconds * 1000)
    return expires_ms <= threshold_ms


def _access_token_needs_refresh(
    encryptor: TokenEncryptor,
    account: Account,
    *,
    now: datetime | None = None,
) -> bool:
    try:
        access_token = encryptor.decrypt(account.access_token_encrypted)
    except Exception:
        return False
    expires_ms = _stored_expiry_ms(account, access_token)
    if expires_ms is None:
        return False
    return _expires_within(expires_ms, margin_seconds=_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS, now=now)


def access_token_hard_expired(
    encryptor: TokenEncryptor,
    account: Account,
    *,
    now: datetime | None = None,
) -> bool:

    if not account.access_token_encrypted:
        return True
    try:
        access_token = encryptor.decrypt(account.access_token_encrypted)
    except Exception:
        return True
    expires_ms = _stored_expiry_ms(account, access_token)
    if expires_ms is None:
        return False
    return _expires_within(expires_ms, margin_seconds=_ACCESS_TOKEN_SERVING_MARGIN_SECONDS, now=now)


def _account_needs_refresh(encryptor: TokenEncryptor, account: Account, *, now: datetime | None = None) -> bool:
    current = to_utc_naive(now) if now is not None else utcnow()
    if _access_token_needs_refresh(encryptor, account, now=current):
        return True
    try:
        provider = get_provider(account.provider)
    except ProviderLookupError:
        return should_refresh(account.last_refresh, now=current)
    interval_seconds = provider.access_token_refresh_interval_seconds
    if interval_seconds is not None:
        last = to_utc_naive(account.last_refresh)
        return current - last > timedelta(seconds=interval_seconds)
    return should_refresh(account.last_refresh, now=current)


def _clear_refresh_singleflight_state() -> None:
    _REFRESH_SINGLEFLIGHT.clear()


def expiry_datetime(
    access_token: str, expires_in: int | None = None, *, now: datetime | None = None
) -> datetime | None:
    expires_ms = token_expiry_epoch_ms(access_token)
    if expires_ms is not None:
        return datetime.utcfromtimestamp(expires_ms / 1000)
    if expires_in is not None and expires_in > 0:
        return (now or utcnow()) + timedelta(seconds=expires_in)
    return None


def _stored_expiry_ms(account: Account, access_token: str) -> int | None:
    if account.access_expires_at is not None:
        return naive_utc_to_epoch(to_utc_naive(account.access_expires_at)) * 1000
    return token_expiry_epoch_ms(access_token)
