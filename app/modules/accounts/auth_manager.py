from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import (
    RefreshError,
    TokenRefreshResult,
    get_token_refresh_timeout_override,
    pop_token_refresh_timeout_override,
    push_token_refresh_timeout_override,
    refresh_access_token,
)
from app.core.config.settings import get_settings
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
from app.db import session as db_session
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts import refresh_lock
from app.modules.accounts.auth_exchange import AccountExchangeRefresh as AccountExchangeRefresh
from app.modules.accounts.auth_persistence import AccountRefreshPersistence as AccountRefreshPersistence
from app.modules.accounts.auth_preflight import AccountRefreshPreflight as AccountRefreshPreflight
from app.modules.accounts.auth_support import (
    _ACCESS_TOKEN_REFRESH_MARGIN_SECONDS as _ACCESS_TOKEN_REFRESH_MARGIN_SECONDS,
)
from app.modules.accounts.auth_support import (
    _ACCESS_TOKEN_SERVING_MARGIN_SECONDS as _ACCESS_TOKEN_SERVING_MARGIN_SECONDS,
)
from app.modules.accounts.auth_support import _CLAIM_RELEASE_MAX_ATTEMPTS as _CLAIM_RELEASE_MAX_ATTEMPTS
from app.modules.accounts.auth_support import _CLAIM_RELEASE_RETRY_BASE_SECONDS as _CLAIM_RELEASE_RETRY_BASE_SECONDS
from app.modules.accounts.auth_support import _FINAL_PERSIST_MAX_ATTEMPTS as _FINAL_PERSIST_MAX_ATTEMPTS
from app.modules.accounts.auth_support import _FINAL_PERSIST_RETRY_BASE_SECONDS as _FINAL_PERSIST_RETRY_BASE_SECONDS
from app.modules.accounts.auth_support import _REFRESH_SINGLEFLIGHT as _REFRESH_SINGLEFLIGHT
from app.modules.accounts.auth_support import _TERMINAL_REFRESH_STATUSES as _TERMINAL_REFRESH_STATUSES
from app.modules.accounts.auth_support import _TOKEN_CAS_MAX_ATTEMPTS as _TOKEN_CAS_MAX_ATTEMPTS
from app.modules.accounts.auth_support import _TOKEN_REFRESH_CLAIM_POLL_SECONDS as _TOKEN_REFRESH_CLAIM_POLL_SECONDS
from app.modules.accounts.auth_support import _TOKEN_REFRESH_CLAIM_WAIT_SECONDS as _TOKEN_REFRESH_CLAIM_WAIT_SECONDS
from app.modules.accounts.auth_support import AccountNotOwnedError as AccountNotOwnedError
from app.modules.accounts.auth_support import AccountsRepositoryPort as AccountsRepositoryPort
from app.modules.accounts.auth_support import RefreshAdmissionLeasePort as RefreshAdmissionLeasePort
from app.modules.accounts.auth_support import _access_token_needs_refresh as _access_token_needs_refresh
from app.modules.accounts.auth_support import _account_has_active_proxy_binding as _account_has_active_proxy_binding
from app.modules.accounts.auth_support import _account_needs_refresh as _account_needs_refresh
from app.modules.accounts.auth_support import _adopt_account_row as _adopt_account_row
from app.modules.accounts.auth_support import (
    _call_with_supported_optional_kwargs as _call_with_supported_optional_kwargs,
)
from app.modules.accounts.auth_support import _chatgpt_account_id_from_id_token as _chatgpt_account_id_from_id_token
from app.modules.accounts.auth_support import _clean_optional as _clean_optional
from app.modules.accounts.auth_support import _clear_refresh_singleflight_state as _clear_refresh_singleflight_state
from app.modules.accounts.auth_support import _decrypt_refresh_token_plaintext as _decrypt_refresh_token_plaintext
from app.modules.accounts.auth_support import _exchange_timeout_seconds as _exchange_timeout_seconds
from app.modules.accounts.auth_support import _expires_within as _expires_within
from app.modules.accounts.auth_support import _is_genuine_peer_rotation as _is_genuine_peer_rotation
from app.modules.accounts.auth_support import _refresh_singleflight_key as _refresh_singleflight_key
from app.modules.accounts.auth_support import _refresh_token_material_changed as _refresh_token_material_changed
from app.modules.accounts.auth_support import _refresh_token_material_fingerprint as _refresh_token_material_fingerprint
from app.modules.accounts.auth_support import _RefreshSingleflight as _RefreshSingleflight
from app.modules.accounts.auth_support import _RefreshSingleflightKey as _RefreshSingleflightKey
from app.modules.accounts.auth_support import _snapshot as _snapshot
from app.modules.accounts.auth_support import _stored_expiry_ms as _stored_expiry_ms
from app.modules.accounts.auth_support import _terminal_status_refresh_error as _terminal_status_refresh_error
from app.modules.accounts.auth_support import access_token_hard_expired as access_token_hard_expired
from app.modules.accounts.auth_support import expiry_datetime as expiry_datetime
from app.modules.accounts.auth_support import is_locally_owned as is_locally_owned
from app.modules.accounts.auth_support import logger as logger
from app.modules.accounts.refresh_claims import RefreshClaimCoordinatorPort, get_refresh_claim_coordinator
from app.modules.accounts.repository import EXCHANGE_RESET_REASONS


class AuthManager(AccountRefreshPreflight, AccountRefreshPersistence, AccountExchangeRefresh):
    def __init__(
        self,
        repo: AccountsRepositoryPort,
        *,
        acquire_refresh_admission: Callable[[], Awaitable[RefreshAdmissionLeasePort]] | None = None,
        refresh_repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepositoryPort]] | None = None,
        refresh_claims: RefreshClaimCoordinatorPort | None = None,
        redact_sensitive_details: bool = False,
    ) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()
        self._acquire_refresh_admission = acquire_refresh_admission
        self._redact_sensitive_details = redact_sensitive_details
        self._refresh_repo_factory = refresh_repo_factory
        self._refresh_claims = refresh_claims

    async def ensure_fresh(self, account: Account, *, force: bool = False, recover_uncertain: bool = False) -> Account:
        settings = get_settings()
        latest = await self._repo.reload_by_id(account.id)
        if latest is not None:
            account = latest
        account = _snapshot(self._repo, account)
        if not is_locally_owned(account, settings):
            if force or access_token_hard_expired(self._encryptor, account):
                raise AccountNotOwnedError(account.id, account.owner_instance, settings.local_instance_id)
            return await self._ensure_chatgpt_account_id(account)
        recover_exchange = False
        if account.status == AccountStatus.EXCHANGE_UNCERTAIN:
            if recover_uncertain and isinstance(getattr(self._repo, "session", None), db_session.AsyncSession):
                intent = await self._repo.exchange_intent_window(account.id, _exchange_timeout_seconds())
                recover_exchange = (
                    intent is not None
                    and intent.token_hash
                    == _refresh_token_material_fingerprint(self._encryptor, account.refresh_token_encrypted)
                    and intent.ready
                    and (
                        intent.reason in EXCHANGE_RESET_REASONS
                        or (not intent.replay and intent.reason not in {"unsaved", "auto_exhausted"})
                    )
                )
            if not force and (not recover_exchange) and (not access_token_hard_expired(self._encryptor, account)):
                return await self._ensure_chatgpt_account_id(account)
        if force or recover_exchange or _account_needs_refresh(self._encryptor, account):
            account = await _REFRESH_SINGLEFLIGHT.run(
                _refresh_singleflight_key(self._encryptor, account), lambda: self._run_refresh(account)
            )
        return await self._ensure_chatgpt_account_id(account)

    async def _run_refresh(self, account: Account) -> Account:
        if self._refresh_repo_factory is None:
            owned = AuthManager(
                self._repo,
                acquire_refresh_admission=self._acquire_refresh_admission,
                refresh_claims=self._refresh_claims,
                redact_sensitive_details=True,
            )
            return await owned.refresh_account(account)
        async with self._refresh_repo_factory() as repo:
            owned = AuthManager(
                repo,
                acquire_refresh_admission=self._acquire_refresh_admission,
                refresh_claims=self._refresh_claims,
                redact_sensitive_details=True,
            )
            return await owned.refresh_account(account)

    def _diagnostic_value(self, value: str | None) -> str | None:
        if self._redact_sensitive_details and value is not None:
            return "<redacted>"
        return value

    async def refresh_account(self, account: Account) -> Account:
        try:
            get_provider(account.provider)
        except ProviderLookupError as exc:
            raise RefreshError("unsupported_provider", str(exc), True) from exc
        if not is_locally_owned(account, get_settings()):
            raise AccountNotOwnedError(account.id, account.owner_instance, get_settings().local_instance_id)
        claims = self._refresh_claims if self._refresh_claims is not None else get_refresh_claim_coordinator()
        if claims is None:
            return await self._perform_refresh(account, refresh_token_encrypted=account.refresh_token_encrypted)
        return await self._refresh_account_with_claim(account, claims)

    async def _refresh_account_with_claim(self, account: Account, claims: RefreshClaimCoordinatorPort) -> Account:
        settings = get_settings()
        requested_fingerprint = _refresh_token_material_fingerprint(self._encryptor, account.refresh_token_encrypted)
        wait_seconds = _TOKEN_REFRESH_CLAIM_WAIT_SECONDS
        caller_budget = get_token_refresh_timeout_override()
        start = time.monotonic()
        caller_deadline: float | None = None
        if caller_budget is not None:
            caller_budget = max(0.0, caller_budget)
            caller_deadline = start + caller_budget
            wait_seconds = min(wait_seconds, caller_budget)
        deadline = start + wait_seconds
        poll_seconds = _TOKEN_REFRESH_CLAIM_POLL_SECONDS
        while True:
            if await claims.try_acquire(
                account.id, ttl_seconds=settings.token_refresh_claim_ttl_seconds, owner=requested_fingerprint
            ):
                claim_ttl = max(0.0, float(settings.token_refresh_claim_ttl_seconds))
                persist_deadline = time.monotonic() + claim_ttl
                if caller_deadline is not None:
                    persist_deadline = min(persist_deadline, caller_deadline)
                try:
                    latest = await self._repo.get_by_id_fresh(account.id)
                    if latest is not None:
                        if (
                            _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                            != requested_fingerprint
                        ):
                            return _adopt_account_row(account, latest)
                        if latest.status in _TERMINAL_REFRESH_STATUSES:
                            _adopt_account_row(account, latest)
                            raise _terminal_status_refresh_error(account)
                    fresh_material = (
                        latest.refresh_token_encrypted if latest is not None else account.refresh_token_encrypted
                    )
                    if caller_deadline is not None:
                        remaining_budget = caller_deadline - time.monotonic()
                        if remaining_budget <= 0:
                            raise RefreshError(
                                "refresh_claim_timeout",
                                f"Token refresh for account {account.id} exhausted its {caller_budget:.3f}s budget waiting for a peer replica's refresh claim before the upstream exchange could start",
                                False,
                                transport_error=True,
                            )
                        override_token = push_token_refresh_timeout_override(remaining_budget)
                        try:
                            return await self._perform_refresh(
                                account, refresh_token_encrypted=fresh_material, deadline=persist_deadline
                            )
                        finally:
                            pop_token_refresh_timeout_override(override_token)
                    return await self._perform_refresh(
                        account, refresh_token_encrypted=fresh_material, deadline=persist_deadline
                    )
                finally:
                    await self._release_claim_quietly(claims, account.id, owner=requested_fingerprint)
            latest = await self._repo.get_by_id_fresh(account.id)
            if (
                latest is not None
                and _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                != requested_fingerprint
            ):
                return _adopt_account_row(account, latest)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RefreshError(
                    "refresh_claim_timeout",
                    f"Token refresh for account {account.id} is claimed by another replica; timed out waiting {wait_seconds:.3f}s for its rotation",
                    False,
                    transport_error=True,
                )
            await asyncio.sleep(min(poll_seconds, remaining))

    async def _release_claim_quietly(self, claims: RefreshClaimCoordinatorPort, account_id: str, *, owner: str) -> None:
        last_exc: Exception | None = None
        for attempt in range(_CLAIM_RELEASE_MAX_ATTEMPTS):
            try:
                await claims.release(account_id, owner=owner)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _CLAIM_RELEASE_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_CLAIM_RELEASE_RETRY_BASE_SECONDS * 2**attempt)
        logger.warning(
            "Failed to release refresh claim for account_id=%s after %d attempts; leaving it to expire by TTL (the committed refresh result is unaffected)",
            self._diagnostic_value(account_id),
            _CLAIM_RELEASE_MAX_ATTEMPTS,
            exc_info=None if self._redact_sensitive_details else last_exc,
        )

    async def _perform_refresh(
        self, account: Account, *, refresh_token_encrypted: bytes, deadline: float | None = None
    ) -> Account:
        provider = get_provider(account.provider)
        async with refresh_lock._cross_process_refresh_lock(account.id):
            return await self._refresh_account_locked(account, provider, deadline=deadline)

    async def _refresh_tokens(
        self,
        refresh_token: str,
        *,
        account: Account,
        provider: Provider,
        on_exchange_start: Callable[[], None] | None = None,
        deadline: float | None = None,
    ) -> TokenRefreshResult:
        budget = get_token_refresh_timeout_override()
        if budget is not None:
            caller_deadline = time.monotonic() + max(0.0, budget)
            deadline = min(deadline, caller_deadline) if deadline is not None else caller_deadline
        refresh_lease = await self._acquire_refresh_admission_bounded(account, deadline)
        exchange_override: contextvars.Token[float | None] | None = None
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if refresh_lease is not None:
                    refresh_lease.release()
                raise RefreshError(
                    "refresh_claim_timeout",
                    "Refresh budget exhausted before token exchange",
                    False,
                    transport_error=True,
                    phase=ExchangePhase.PRE_SEND,
                )
            exchange_override = push_token_refresh_timeout_override(remaining)
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
                if route is None and await _account_has_active_proxy_binding(session, account.id):
                    raise RefreshError(
                        "upstream_proxy_unavailable",
                        "Account has an active proxy binding but no route resolved",
                        False,
                        transport_error=True,
                        upstream_proxy_fail_closed_reason="binding_route_unavailable",
                        phase=ExchangePhase.PRE_SEND,
                    )
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
            if exchange_override is not None:
                pop_token_refresh_timeout_override(exchange_override)
            if refresh_lease is not None:
                refresh_lease.release()
