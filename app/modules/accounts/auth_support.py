from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol, TypeAlias

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import token_expiry_epoch_ms
from app.core.auth.refresh import RefreshError, should_refresh
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import OPENAI_PROVIDER_NAME, ProviderLookupError, get_provider
from app.core.utils.shared_future import wait_on_shared_future
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountProxyBinding, AccountStatus


class AccountsRepositoryPort(Protocol):
    async def get_by_id(self, account_id: str) -> Account | None: ...

    async def get_by_id_fresh(self, account_id: str) -> Account | None: ...

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
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool: ...

    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool: ...

    async def update_account_metadata(
        self,
        account_id: str,
        *,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        last_refresh: datetime | None = None,
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
        chatgpt_user_id: str | None = None,
    ) -> bool: ...

    async def workspace_slot_taken(
        self, *, account_id: str, email: str, chatgpt_account_id: str | None, workspace_id: str
    ) -> bool: ...


class RefreshAdmissionLeasePort(Protocol):
    def release(self) -> None: ...


logger = logging.getLogger(__name__)
_TOKEN_CAS_MAX_ATTEMPTS = 5
_FINAL_PERSIST_MAX_ATTEMPTS = 3
_FINAL_PERSIST_RETRY_BASE_SECONDS = 0.02
_CLAIM_RELEASE_MAX_ATTEMPTS = 3
_CLAIM_RELEASE_RETRY_BASE_SECONDS = 0.05
_TOKEN_REFRESH_CLAIM_WAIT_SECONDS = 8.0
_TOKEN_REFRESH_CLAIM_POLL_SECONDS = 0.25
_TERMINAL_REFRESH_STATUSES = frozenset({AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED})
_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 300
_ACCESS_TOKEN_SERVING_MARGIN_SECONDS = 300


class AccountNotOwnedError(Exception):
    def __init__(self, account_id: str, owner_instance: str | None, local_instance_id: str) -> None:
        message = f"Account {account_id} is owned by instance {owner_instance!r}, not the local instance {local_instance_id!r}; refusing to refresh"
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
        self, key: _RefreshSingleflightKey, factory: Callable[[], Coroutine[object, object, Account]]
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
            if task is not None and task.done() and (not task.cancelled()) and (task.exception() is None):
                pass
            elif task is None or task.done():
                task = asyncio.create_task(factory())
                self._inflight[key] = task
                task.add_done_callback(lambda done, *, cache_key=key: self._schedule_complete(cache_key, done))
        assert task is not None
        return await wait_on_shared_future(task)

    def _schedule_complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        asyncio.create_task(self._complete(key, task))

    async def _complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        try:
            async with self._lock:
                current = self._inflight.get(key)
                if current is not task:
                    if not task.cancelled():
                        task.exception()
                    return
                self._inflight.pop(key, None)
                if task.cancelled():
                    self._recent_failures.pop(key, None)
                    return
                try:
                    task.result()
                except RefreshError as exc:
                    ttl = max(0.0, float(get_settings().proxy_refresh_failure_cooldown_seconds))
                    if ttl > 0 and (not exc.transport_error):
                        self._recent_failures[key] = (time.monotonic() + ttl, (exc.code, exc.message, exc.is_permanent))
                    else:
                        self._recent_failures.pop(key, None)
                except BaseException:
                    self._recent_failures.pop(key, None)
                else:
                    self._recent_failures.pop(key, None)
        except BaseException:
            logger.error("Refresh singleflight completion cleanup failed")

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


def _exchange_timeout_seconds() -> float:
    settings = get_settings()
    return max(settings.oauth_timeout_seconds, settings.token_refresh_timeout_seconds, 30.0)


def _chatgpt_account_id_from_id_token(id_token: str) -> str | None:
    return get_provider(OPENAI_PROVIDER_NAME).account_metadata_from_id_token(id_token).account_id


def _refresh_singleflight_key(encryptor: TokenEncryptor, account: Account) -> _RefreshSingleflightKey:
    return (account.id, _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted))


def _adopt_account_row(target: Account, source: Account) -> Account:
    settings = get_settings()
    if not is_locally_owned(source, settings):
        raise AccountNotOwnedError(source.id, source.owner_instance, settings.local_instance_id)
    if target is source:
        return target
    for column in Account.__table__.columns:
        if column.name in ("id", "created_at"):
            continue
        setattr(target, column.name, getattr(source, column.name))
    return target


def _terminal_status_refresh_error(account: Account) -> RefreshError:
    code = "account_deactivated" if account.status == AccountStatus.DEACTIVATED else "refresh_token_invalidated"
    reason = account.deactivation_reason or PERMANENT_FAILURE_CODES.get(code, "re-login required")
    return RefreshError(
        code,
        f"Token refresh for account {account.id} not attempted: a prior refresh-claim holder already flagged the account {account.status.value} without rotating the refresh token ({reason}); reusing the unchanged consumed token would repeat a permanent failure",
        True,
    )


def _expires_within(expires_ms: int, *, margin_seconds: float, now: datetime | None = None) -> bool:
    current = to_utc_naive(now) if now is not None else utcnow()
    threshold_ms = naive_utc_to_epoch(current) * 1000 + margin_seconds * 1000
    return expires_ms <= threshold_ms


def _access_token_needs_refresh(encryptor: TokenEncryptor, account: Account, *, now: datetime | None = None) -> bool:
    try:
        access_token = encryptor.decrypt(account.access_token_encrypted)
    except Exception:
        return False
    expires_ms = _stored_expiry_ms(account, access_token)
    if expires_ms is None:
        return False
    return _expires_within(expires_ms, margin_seconds=_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS, now=now)


def access_token_hard_expired(encryptor: TokenEncryptor, account: Account, *, now: datetime | None = None) -> bool:
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


def _snapshot(repo: object, account: Account) -> Account:
    if not hasattr(repo, "session"):
        return account
    return Account(**{column.key: getattr(account, column.key) for column in Account.__table__.columns})


def _refresh_token_material_changed(
    encryptor: TokenEncryptor, latest_refresh_token_encrypted: bytes, current_refresh_token_encrypted: bytes
) -> bool:
    return _refresh_token_material_fingerprint(
        encryptor, latest_refresh_token_encrypted
    ) != _refresh_token_material_fingerprint(encryptor, current_refresh_token_encrypted)


def _refresh_token_material_fingerprint(encryptor: TokenEncryptor, refresh_token_encrypted: bytes) -> str:
    try:
        material = encryptor.decrypt(refresh_token_encrypted).encode("utf-8")
    except Exception:
        material = refresh_token_encrypted
    return sha256(material).hexdigest()


def _decrypt_refresh_token_plaintext(encryptor: TokenEncryptor, refresh_token_encrypted: bytes) -> str | None:
    try:
        return encryptor.decrypt(refresh_token_encrypted)
    except Exception:
        return None


def _is_genuine_peer_rotation(consumed_plaintext: str | None, latest_plaintext: str | None) -> bool:
    return consumed_plaintext is not None and latest_plaintext is not None and (latest_plaintext != consumed_plaintext)


def _clean_optional(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


async def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]], /, *args: Any, optional_kwargs: Mapping[str, Any], **required_kwargs: Any
) -> Any:
    kwargs = dict(required_kwargs)
    kwargs.update(optional_kwargs)
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    accepts_var_keyword = signature is not None and any(
        (parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
    )
    if signature is not None and (not accepts_var_keyword):
        for name in optional_kwargs:
            if name not in signature.parameters:
                kwargs.pop(name, None)
    return await func(*args, **kwargs)


def _clear_refresh_singleflight_state() -> None:
    _REFRESH_SINGLEFLIGHT.clear()


async def _account_has_active_proxy_binding(session: AsyncSession, account_id: str) -> bool:
    try:
        result = await session.execute(
            select(AccountProxyBinding.id)
            .where(AccountProxyBinding.account_id == account_id, AccountProxyBinding.is_active.is_(True))
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
    except OperationalError:
        return False


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
