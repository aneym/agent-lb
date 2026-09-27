from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol, TypeAlias

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.auth import DEFAULT_PLAN, token_expiry_epoch_ms
from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import (
    RefreshError,
    TokenRefreshResult,
    classify_refresh_error,
    refresh_access_token,
    should_refresh,
)
from app.core.balancer import account_status_for_permanent_failure, permanent_failure_reason
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
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
# Mirrors and uncertain exchanges cannot refresh; leave time for an owner or
# operator to recover before the token expires.
_ACCESS_TOKEN_SERVING_MARGIN_SECONDS = 300


class AccountNotOwnedError(Exception):
    """Raised when a refresh is required for an account this instance does not own.

    Instance federation restricts OAuth refresh execution to a single owner
    instance per account (openspec instance-federation): non-owners must
    never call the provider's refresh endpoint, regardless of token age.
    """

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


REFRESH_LOCK_TIMEOUT_SECONDS = 30


def _refresh_lock_key(account_id: str) -> int:
    digest = sha256(f"account-refresh:{account_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


_REFRESH_LOCK_ENGINE: AsyncEngine | None = None


def _refresh_lock_engine() -> AsyncEngine | None:
    """A pool-less engine for refresh locks, or None off Postgres.

    Lock holders must not sit in the shared pools: the locked body needs its own
    repository and route-lookup connections, and enough concurrent refreshes
    holding pool connections would starve their own completion.
    """
    global _REFRESH_LOCK_ENGINE
    if db_session.engine.dialect.name != "postgresql":
        return None
    if _REFRESH_LOCK_ENGINE is None:
        _REFRESH_LOCK_ENGINE = create_async_engine(db_session.engine.url, poolclass=NullPool)
    return _REFRESH_LOCK_ENGINE


@asynccontextmanager
async def _cross_process_refresh_lock(account_id: str) -> AsyncIterator[None]:
    """Serialize one account's token refresh across agent-lb processes.

    The singleflight above only sees its own process. A blue/green restart runs
    a standby and a draining primary against one database, and OpenAI refresh
    tokens are single use: two processes presenting the same token gets the
    second one refresh_token_reused, which marks the account reauth_required.
    The lock is transaction scoped on its own unpooled connection, so a dropped
    connection or a cancelled caller releases it; the holder's refresh is
    committed before it lets go, and the waiter's reload then sees the rotated
    token and skips the provider.
    """
    lock_engine = _refresh_lock_engine()
    if lock_engine is None:
        yield
        return
    async with lock_engine.connect() as conn, conn.begin():
        await conn.execute(text(f"SET LOCAL lock_timeout = '{REFRESH_LOCK_TIMEOUT_SECONDS}s'"))
        try:
            await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _refresh_lock_key(account_id)})
        except Exception as exc:
            raise RefreshError(
                "refresh_lock_timeout",
                f"Another process held the refresh lock for {account_id} over {REFRESH_LOCK_TIMEOUT_SECONDS}s",
                False,
                transport_error=True,
            ) from exc
        yield


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
        # Optional factory yielding a *fresh* accounts repo (own DB session) for
        # the detached, shielded refresh task. When set, the singleflight body
        # runs against this session instead of the request-scoped `repo`, so a
        # caller cancelled by a client disconnect cannot close the session out
        # from under the still-running refresh task and strand a pooled
        # connection. See _run_refresh.
        self._refresh_repo_factory = refresh_repo_factory

    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        settings = get_settings()
        # Selection inputs are cached briefly. Recheck ownership and the token
        # version against the database before returning a cached access token.
        latest = await self._repo.reload_by_id(account.id)
        if latest is not None:
            account = latest
            # Return an independent snapshot: request-scoped rollback on close
            # expires the ORM instance loaded in this repository session.
            account = _snapshot(self._repo, account)
        if not is_locally_owned(account, settings):
            # A mirror cannot refresh; stop serving before its token expires.
            if force or access_token_hard_expired(self._encryptor, account):
                raise AccountNotOwnedError(account.id, account.owner_instance, settings.local_instance_id)
            return await self._ensure_chatgpt_account_id(account)
        if account.status == AccountStatus.EXCHANGE_UNCERTAIN:
            if not force and not access_token_hard_expired(self._encryptor, account):
                return await self._ensure_chatgpt_account_id(account)
        if force or _account_needs_refresh(self._encryptor, account):
            account = await _REFRESH_SINGLEFLIGHT.run(
                _refresh_singleflight_key(self._encryptor, account),
                lambda: self._run_refresh(account),
            )
        return await self._ensure_chatgpt_account_id(account)

    async def _run_refresh(self, account: Account) -> Account:
        """Singleflight body for token refresh.

        Runs inside a detached task that the singleflight keeps alive with
        ``asyncio.shield`` (so concurrent waiters share one refresh and a
        cancelled waiter does not abort it). Because the task outlives the
        caller, it MUST NOT use the caller's request-scoped session: when a
        client disconnects, the caller is cancelled and its
        ``async with get_background_session()`` closes that session, while this
        shielded task keeps running and would then touch a closed,
        concurrently-finalized ``AsyncSession`` (not safe for concurrent use) —
        stranding a pooled connection that never returns. When a
        ``refresh_repo_factory`` is provided, open a fresh session here so the
        refresh write is fully self-contained; otherwise fall back to the bound
        repo (callers whose session is not client-cancellable, e.g. the usage
        refresh scheduler).
        """
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
        async with _cross_process_refresh_lock(account.id):
            return await self._refresh_account_locked(account, provider)

    async def _refresh_account_locked(self, account: Account, provider: Provider) -> Account:
        expected_refresh_token_encrypted = account.refresh_token_encrypted
        latest = await self._repo.reload_by_id(account.id)
        if latest is not None and _refresh_token_material_changed(
            self._encryptor, latest.refresh_token_encrypted, expected_refresh_token_encrypted
        ):
            if not is_locally_owned(latest, get_settings()):
                raise AccountNotOwnedError(latest.id, latest.owner_instance, get_settings().local_instance_id)
            # A delayed caller must not replay a token already rotated by another request.
            return _snapshot(self._repo, latest)
        if latest is None:
            if isinstance(getattr(self._repo, "session", None), db_session.AsyncSession):
                raise RefreshError("account_missing", "Account no longer exists", False)
            latest = account
        if not is_locally_owned(latest, get_settings()):
            raise AccountNotOwnedError(latest.id, latest.owner_instance, get_settings().local_instance_id)
        account = latest
        account_id = account.id
        expected_refresh_token_encrypted = latest.refresh_token_encrypted
        token_hash = _refresh_token_material_fingerprint(self._encryptor, expected_refresh_token_encrypted)
        intent_supported = isinstance(getattr(self._repo, "session", None), db_session.AsyncSession)
        replay = False
        if intent_supported:
            timeout = max(
                get_settings().oauth_timeout_seconds,
                get_settings().token_refresh_timeout_seconds,
                30.0,  # CodexClient's refresh-request timeout is bounded by the configured token timeout.
            )
            intent = await self._repo.exchange_intent_window(account.id, timeout)
            if intent is not None and intent.token_hash != token_hash:
                if account.status != AccountStatus.EXCHANGE_UNCERTAIN and await self._repo.drop_stale_exchange_intent(
                    account.id, intent.token_hash, expected_refresh_token_encrypted
                ):
                    logger.info("Dropped stale exchange intent account_id=%s", account.id)
                    intent = None
                else:
                    raise RefreshError("exchange_intent_conflict", "A previous exchange is unresolved", False)
            if intent is not None:
                if not intent.ready or (
                    intent.reason != "operator_reset" and (intent.replay or intent.reason == "unsaved")
                ):
                    if account.status != AccountStatus.EXCHANGE_UNCERTAIN:
                        await self._repo.mark_exchange_uncertain(
                            account.id, token_hash, expected_refresh_token_encrypted
                        )
                        get_account_selection_cache().invalidate()
                    raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False)
                replay = True
            elif account.status == AccountStatus.EXCHANGE_UNCERTAIN:
                raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False)
            if not await self._repo.begin_exchange(
                account_id,
                token_hash,
                expected_refresh_token_encrypted,
                replay=replay,
                timeout_seconds=timeout if replay else None,
            ):
                latest = await self._repo.reload_by_id(account_id)
                if latest is not None and not is_locally_owned(latest, get_settings()):
                    raise AccountNotOwnedError(latest.id, latest.owner_instance, get_settings().local_instance_id)
                if latest is not None and _refresh_token_material_changed(
                    self._encryptor, latest.refresh_token_encrypted, expected_refresh_token_encrypted
                ):
                    return _snapshot(self._repo, latest)  # a re-login rotated the token first
                if await self._repo.exchange_intent_hash(account_id) == token_hash:
                    await self._repo.mark_exchange_uncertain(account_id, token_hash, expected_refresh_token_encrypted)
                    get_account_selection_cache().invalidate()
                raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False)
        elif account.status == AccountStatus.EXCHANGE_UNCERTAIN:
            raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False)
        exchange_started = False

        def mark_exchange_started() -> None:
            nonlocal exchange_started
            exchange_started = True

        try:
            refresh_token = self._encryptor.decrypt(expected_refresh_token_encrypted)
            result = await self._refresh_tokens(
                refresh_token, account=account, provider=provider, on_exchange_start=mark_exchange_started
            )
        except BaseException as exc:
            if intent_supported:
                phase = exc.phase if isinstance(exc, RefreshError) else ExchangePhase.AMBIGUOUS
                if (
                    not exchange_started
                    or phase == ExchangePhase.PRE_SEND
                    or (
                        phase == ExchangePhase.ANSWERED
                        and isinstance(exc, RefreshError)
                        and not exc.is_permanent
                        and not (exc.status_code is not None and exc.status_code >= 500)
                    )
                ):
                    await self._repo.abort_exchange(account.id, token_hash)
                    if replay:
                        await self._repo.update_status(
                            account.id,
                            AccountStatus.ACTIVE,
                            expected_refresh_token_encrypted=expected_refresh_token_encrypted,
                        )
                    if isinstance(exc, RefreshError):
                        raise
                    raise
                if phase == ExchangePhase.ANSWERED and isinstance(exc, RefreshError) and exc.is_permanent:
                    await self._repo.abort_exchange(account.id, token_hash)
                    status = account_status_for_permanent_failure(exc.code)
                    await self._repo.update_status(
                        account.id,
                        status,
                        permanent_failure_reason(exc.code),
                        expected_refresh_token_encrypted=expected_refresh_token_encrypted,
                    )
                    logger.info("Refresh exchange resolved account_id=%s outcome=reauth_required", account.id)
                    raise
                await self._repo.mark_exchange_uncertain(
                    account.id,
                    token_hash,
                    expected_refresh_token_encrypted,
                    reason="unsaved" if isinstance(exc, RefreshError) and exc.code == "invalid_response" else None,
                )
                get_account_selection_cache().invalidate()
                raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False) from exc
            if not isinstance(exc, RefreshError):
                raise
            is_permanent = exc.is_permanent or classify_refresh_error(exc.code)
            if is_permanent:
                exc.is_permanent = True
                latest = await self._repo.reload_by_id(account.id)
                if latest is not None and _refresh_token_material_changed(
                    self._encryptor,
                    latest.refresh_token_encrypted,
                    expected_refresh_token_encrypted,
                ):
                    return _snapshot(self._repo, latest)
                reason = permanent_failure_reason(exc.code)
                status = account_status_for_permanent_failure(exc.code)
                status_updated = await self._repo.update_status(
                    account.id,
                    status,
                    reason,
                    expected_refresh_token_encrypted=(
                        latest.refresh_token_encrypted if latest is not None else expected_refresh_token_encrypted
                    ),
                )
                if not status_updated:
                    current = await self._repo.reload_by_id(account.id)
                    if current is not None and _refresh_token_material_changed(
                        self._encryptor,
                        current.refresh_token_encrypted,
                        expected_refresh_token_encrypted,
                    ):
                        return _snapshot(self._repo, current)
                    raise
                account.status = status
                account.deactivation_reason = reason
                logger.warning(
                    "OAuth refresh requires reauthentication account_id=%s provider=%s code=%s",
                    account.id,
                    account.provider,
                    exc.code,
                )
            raise

        if provider.requires_id_token and not result.id_token:
            if intent_supported:
                await self._repo.mark_exchange_uncertain(
                    account.id, token_hash, expected_refresh_token_encrypted, reason="unsaved"
                )
            raise RefreshError("invalid_response", "Refresh response missing id token", False)

        new_access_token_encrypted = self._encryptor.encrypt(result.access_token)
        new_refresh_token_encrypted = self._encryptor.encrypt(result.refresh_token)
        account.access_token_encrypted = new_access_token_encrypted
        account.refresh_token_encrypted = new_refresh_token_encrypted
        if result.id_token:
            account.id_token_encrypted = self._encryptor.encrypt(result.id_token)
        else:
            account.id_token_encrypted = None
        account.last_refresh = utcnow()
        account.access_expires_at = _expiry_datetime(result.access_token, result.expires_in, now=account.last_refresh)
        if result.account_id:
            account.chatgpt_account_id = result.account_id
        if result.plan_type is not None:
            account.plan_type = coerce_account_plan_type(
                result.plan_type,
                account.plan_type or DEFAULT_PLAN,
            )
        elif not account.plan_type:
            account.plan_type = DEFAULT_PLAN
        if result.email:
            account.email = result.email
        incoming_workspace_id = _clean_optional(result.workspace_id)
        current_workspace_id = _clean_optional(account.workspace_id)
        next_workspace_id = current_workspace_id
        if incoming_workspace_id and current_workspace_id and current_workspace_id != incoming_workspace_id:
            logger.warning(
                "Refresh payload reported workspace_id=%s for account_id=%s while existing "
                "workspace_id=%s is already set; keeping slot identity",
                incoming_workspace_id,
                account.id,
                current_workspace_id,
            )
            next_workspace_id = current_workspace_id
        elif not current_workspace_id and incoming_workspace_id:
            slot_taken = await self._repo.workspace_slot_taken(
                account_id=account.id,
                email=account.email,
                chatgpt_account_id=account.chatgpt_account_id,
                workspace_id=incoming_workspace_id,
            )
            if slot_taken:
                logger.warning(
                    "Refresh payload reported workspace_id=%s for legacy account_id=%s, but that slot "
                    "is already owned by another account; keeping unknown workspace",
                    incoming_workspace_id,
                    account.id,
                )
            else:
                next_workspace_id = incoming_workspace_id
                account.workspace_id = next_workspace_id
        workspace_matches_current_slot = incoming_workspace_id is None or incoming_workspace_id == next_workspace_id
        if workspace_matches_current_slot and result.workspace_label:
            account.workspace_label = result.workspace_label
        if workspace_matches_current_slot and result.seat_type:
            account.seat_type = result.seat_type

        # A failed write must never flush the rotated in-memory tokens on a later commit.
        if isinstance(getattr(self._repo, "session", None), db_session.AsyncSession):
            self._repo.session.expunge(account)
        # A durable fence may be marked uncertain by a new worker after lock loss.
        tokens_updated = False
        for attempt in range(3):
            try:
                tokens_updated = await self._repo.update_tokens(
                    account.id,
                    access_token_encrypted=account.access_token_encrypted,
                    refresh_token_encrypted=account.refresh_token_encrypted,
                    id_token_encrypted=account.id_token_encrypted,
                    last_refresh=account.last_refresh,
                    plan_type=account.plan_type,
                    email=account.email,
                    chatgpt_account_id=account.chatgpt_account_id,
                    workspace_id=next_workspace_id,
                    workspace_label=account.workspace_label,
                    seat_type=account.seat_type,
                    expected_refresh_token_encrypted=expected_refresh_token_encrypted,
                    access_expires_at=account.access_expires_at,
                    exchange_token_hash=token_hash if intent_supported else None,
                )
            except Exception:
                if attempt == 2:
                    if intent_supported:
                        await self._repo.mark_exchange_uncertain(
                            account.id, token_hash, expected_refresh_token_encrypted, reason="unsaved"
                        )
                    raise RefreshError("refresh_unsaved", "Refreshed tokens could not be stored", False) from None
                await asyncio.sleep(0)
                continue
            if tokens_updated:
                break
            latest = await self._repo.reload_by_id(account.id)
            if latest is not None and _refresh_token_material_changed(
                self._encryptor, latest.refresh_token_encrypted, expected_refresh_token_encrypted
            ):
                return _snapshot(self._repo, latest)
            if attempt < 2:
                await asyncio.sleep(0)
        if replay and tokens_updated:
            logger.info("Refresh exchange resolved account_id=%s outcome=active", account.id)
        if not tokens_updated:
            if intent_supported:
                await self._repo.mark_exchange_uncertain(
                    account.id, token_hash, expected_refresh_token_encrypted, reason="unsaved"
                )
            raise RefreshError("refresh_unsaved", "Refreshed tokens could not be stored", False)
        get_account_selection_cache().invalidate()
        if hasattr(self._repo, "session"):
            stored = await self._repo.reload_by_id(account.id)
            if stored is not None:
                account = stored
        return _snapshot(self._repo, account)

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
                return await _call_with_supported_optional_kwargs(
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
            return await _call_with_supported_optional_kwargs(
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
                    return _snapshot(self._repo, latest)
        except Exception:
            logger.warning("Failed to persist chatgpt_account_id account_id=%s", account.id, exc_info=True)
        return account


def _chatgpt_account_id_from_id_token(id_token: str) -> str | None:
    return get_provider(OPENAI_PROVIDER_NAME).account_metadata_from_id_token(id_token).account_id


def _refresh_singleflight_key(encryptor: TokenEncryptor, account: Account) -> _RefreshSingleflightKey:
    return (account.id, _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted))


def _expires_within(expires_ms: int, *, margin_seconds: float, now: datetime | None = None) -> bool:
    """True when expires_ms is within margin_seconds of `now` (or already past)."""
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
    """True when a mirror or uncertain exchange cannot safely serve its access token."""
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
    """Copy a row out of a session-backed repository before returning it.

    Background and request sessions roll back an open transaction before they
    close, which expires every row they loaded; a caller that reads the row
    afterwards raises DetachedInstanceError. Rows from repositories without a
    session (test fakes) are returned as they are.
    """
    if not hasattr(repo, "session"):
        return account
    return Account(**{column.key: getattr(account, column.key) for column in Account.__table__.columns})


def _refresh_token_material_changed(
    encryptor: TokenEncryptor,
    latest_refresh_token_encrypted: bytes,
    current_refresh_token_encrypted: bytes,
) -> bool:
    return _refresh_token_material_fingerprint(
        encryptor,
        latest_refresh_token_encrypted,
    ) != _refresh_token_material_fingerprint(
        encryptor,
        current_refresh_token_encrypted,
    )


def _refresh_token_material_fingerprint(encryptor: TokenEncryptor, refresh_token_encrypted: bytes) -> str:
    try:
        material = encryptor.decrypt(refresh_token_encrypted).encode("utf-8")
    except Exception:
        material = refresh_token_encrypted
    return sha256(material).hexdigest()


def _clean_optional(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


async def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> Any:
    kwargs = dict(required_kwargs)
    kwargs.update(optional_kwargs)
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    accepts_var_keyword = signature is not None and any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    if signature is not None and not accepts_var_keyword:
        for name in optional_kwargs:
            if name not in signature.parameters:
                kwargs.pop(name, None)
    return await func(*args, **kwargs)


def _clear_refresh_singleflight_state() -> None:
    _REFRESH_SINGLEFLIGHT.clear()


def _expiry_datetime(
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
