from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Callable

from app.core.anthropic.oauth import build_anthropic_authorization_url, exchange_anthropic_authorization_code
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.clients.oauth import (
    OAuthError,
    OAuthTokens,
    build_authorization_url,
    exchange_authorization_code,
    exchange_device_token,
    generate_pkce_pair,
    request_device_code,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME, ProviderLookupError, get_provider
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.db.session import get_background_session
from app.modules.accounts.identity_repository import AccountIdentityConflictError
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth.callbacks import (
    _ACCOUNT_IDENTITY_CONFLICT_MESSAGE,
    OAuthCallbackHandlers,
)
from app.modules.oauth.callbacks import (
    _error_html as _error_html,
)
from app.modules.oauth.schemas import (
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)
from app.modules.oauth.state import (
    _MAX_RETAINED_TERMINAL_OAUTH_FLOWS as _MAX_RETAINED_TERMINAL_OAUTH_FLOWS,
)
from app.modules.oauth.state import (
    OAuthCallbackServer,
    OAuthState,
    OAuthStateStore,
)
from app.modules.oauth.token_account import account_from_tokens
from app.modules.proxy.account_cache import get_account_selection_cache

_async_sleep = asyncio.sleep
logger = logging.getLogger(__name__)
_PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS = 15 * 60
_CALLBACK_PORT_RELEASE_GRACE_SECONDS = 1.0


async def _oauth_route() -> ResolvedUpstreamRoute | None:
    async with get_background_session() as session:
        try:
            return await resolve_upstream_route(
                session,
                account_id=None,
                operation="oauth",
                scope="bootstrap",
            )
        except UpstreamProxyRouteError as exc:
            raise OAuthError(exc.reason, str(exc), status_code=502) from exc


_OAUTH_STORE = OAuthStateStore()


async def pending_oauth_flow_ages() -> list[float]:

    async with _OAUTH_STORE.lock:
        return _OAUTH_STORE.pending_flow_ages_locked()


class OauthService(OAuthCallbackHandlers):
    def __init__(
        self,
        accounts_repo: AccountsRepository,
        repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepository]] | None = None,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._encryptor = TokenEncryptor()
        self._store = _OAUTH_STORE
        self._repo_factory = repo_factory

    async def start_oauth(self, request: OauthStartRequest) -> OauthStartResponse:
        force_method = (request.force_method or "").lower()
        try:
            provider = get_provider(request.provider)
        except ProviderLookupError as exc:
            raise OAuthError("unsupported_provider", str(exc), 400) from exc
        if not force_method:
            accounts = await self._accounts_repo.list_accounts()
            has_openai_account = any(
                getattr(account, "provider", OPENAI_PROVIDER_NAME) == OPENAI_PROVIDER_NAME for account in accounts
            )
            if provider.name == OPENAI_PROVIDER_NAME and has_openai_account:
                server: OAuthCallbackServer | None = None
                stop_task: asyncio.Task[None] | None = None
                async with self._store.lock:
                    server = self._store._cleanup_locked(clear_callback_server=False)
                    self._store._state = OAuthState(status="success")
                    if server is not None:
                        stop_task = self._start_callback_server_stop_locked(server)
                if server is not None and stop_task is not None:
                    await self._finish_callback_server_stop(server, stop_task)
                return OauthStartResponse(method="browser")

        if force_method == "device":
            if provider.name != OPENAI_PROVIDER_NAME:
                raise OAuthError(
                    "unsupported_provider_flow",
                    "Device login is only supported for OpenAI accounts.",
                    400,
                )
            return await self._start_device_flow()

        try:
            if provider.name == OPENAI_PROVIDER_NAME:
                return await self._start_browser_flow()
            return await self._start_browser_flow(provider.name)
        except OSError:
            if provider.name != OPENAI_PROVIDER_NAME:
                raise
            return await self._start_device_flow()

    async def oauth_status(self, flow_id: str | None = None) -> OauthStatusResponse:
        async with self._store.lock:
            state = self._store.get_flow_locked(flow_id)
            if state is None:
                state = self._store.state if flow_id is None else OAuthState(status="pending")
            status = state.status if state.status != "idle" else "pending"
            return OauthStatusResponse(status=status, error_message=state.error_message)

    async def complete_oauth(self, request: OauthCompleteRequest | None = None) -> OauthCompleteResponse:
        payload = request or OauthCompleteRequest()
        async with self._store.lock:
            flow = self._store.get_flow_locked(payload.flow_id)
            state = flow
            if state is None:
                state = self._store.state if payload.flow_id is None else OAuthState(status="pending")
            if payload.device_auth_id and flow is not None:
                flow.device_auth_id = payload.device_auth_id
            if payload.user_code and flow is not None:
                flow.user_code = payload.user_code
            if flow is not None:
                self._store.set_latest_flow_locked(flow)
            if state.status == "success":
                return OauthCompleteResponse(status="success")
            if state.method != "device":
                return OauthCompleteResponse(status="pending")
            if not self._ensure_device_poll_task_locked(state):
                if flow is not None:
                    self._store.set_flow_status_locked(
                        flow,
                        status="error",
                        error_message="Device code flow is not initialized.",
                    )
                else:
                    state.status = "error"
                    state.error_message = "Device code flow is not initialized."
                return OauthCompleteResponse(status="error")
            return OauthCompleteResponse(status="pending")

    async def _start_browser_flow(self, provider_name: str = OPENAI_PROVIDER_NAME) -> OauthStartResponse:
        flow_id = secrets.token_urlsafe(12)
        code_verifier, code_challenge = generate_pkce_pair()
        provider = get_provider(provider_name)
        state_token = secrets.token_urlsafe(32 if provider.name == ANTHROPIC_PROVIDER_NAME else 16)
        oauth_config = provider.oauth_config()
        if provider.name == ANTHROPIC_PROVIDER_NAME:
            authorization_url = build_anthropic_authorization_url(
                state=state_token,
                code_challenge=code_challenge,
                authorize_url=oauth_config.authorize_url,
                client_id=oauth_config.client_id,
                redirect_uri=oauth_config.redirect_uri,
                scope=oauth_config.scope,
                extra_params=oauth_config.authorization_extra_params,
            )
        else:
            authorization_url = build_authorization_url(
                state=state_token,
                code_challenge=code_challenge,
                base_url=oauth_config.auth_base_url,
                client_id=oauth_config.client_id,
                originator=oauth_config.originator,
                redirect_uri=oauth_config.redirect_uri,
                scope=oauth_config.scope,
                extra_params=oauth_config.authorization_extra_params,
            )
        async with self._store.lock:
            self._store.remember_flow_locked(
                OAuthState(
                    flow_id=flow_id,
                    status="pending",
                    method="browser",
                    provider=provider.name,
                    state_token=state_token,
                    code_verifier=code_verifier,
                    started_at=time.time(),
                    expires_at=time.time() + _PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS,
                )
            )

            asyncio.get_running_loop().call_later(
                _PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS + _CALLBACK_PORT_RELEASE_GRACE_SECONDS,
                lambda: asyncio.ensure_future(self._stop_callback_server_if_idle()),
            )

        bind_task = asyncio.ensure_future(self._ensure_callback_server(flow_id))
        self._store._callback_server_bind_tasks.add(bind_task)
        bind_task.add_done_callback(self._store._callback_server_bind_tasks.discard)
        await asyncio.shield(bind_task)

        return OauthStartResponse(
            flow_id=flow_id,
            method="browser",
            authorization_url=authorization_url,
            callback_url=oauth_config.redirect_uri,
        )

    async def _ensure_callback_server(self, flow_id: str) -> None:
        settings = get_settings()
        callback_server: OAuthCallbackServer | None = None

        async with self._store._callback_server_start_lock:
            while True:
                await self._wait_for_callback_server_stop()
                async with self._store.lock:
                    if self._store._callback_server_stop_task is not None:
                        continue
                    if self._store._callback_server is None:
                        callback_server = OAuthCallbackServer(
                            self._handle_callback,
                            host=settings.oauth_callback_host,
                            port=settings.oauth_callback_port,
                        )
                    break

            if callback_server is not None:
                try:
                    await callback_server.start()
                except OSError:
                    logger.warning(
                        "OAuth callback port %s is busy; this sign-in can finish only by pasting the callback URL",
                        settings.oauth_callback_port,
                    )
                except Exception:
                    await callback_server.stop()
                    await self._set_error("Could not open the OAuth callback listener.", flow_id)
                    raise
                else:
                    async with self._store.lock:
                        self._store._callback_server = callback_server
                        self._store._callback_server_bound_at = time.time()
        if callback_server is not None:
            await self._stop_callback_server_if_idle()

    async def _start_device_flow(self) -> OauthStartResponse:
        flow_id = secrets.token_urlsafe(12)
        try:
            route = await _oauth_route()
            device = await request_device_code(route=route, allow_direct_egress=route is None)
        except OAuthError as exc:
            await self._set_error(exc.message)
            raise

        async with self._store.lock:
            flow = OAuthState(
                flow_id=flow_id,
                status="pending",
                method="device",
                device_auth_id=device.device_auth_id,
                user_code=device.user_code,
                interval_seconds=device.interval_seconds,
                started_at=time.time(),
                expires_at=time.time() + device.expires_in_seconds,
            )
            self._store.remove_pending_device_flows_locked()
            self._store.remember_flow_locked(flow)
            self._ensure_device_poll_task_locked(flow)

        return OauthStartResponse(
            flow_id=flow_id,
            method="device",
            verification_url=device.verification_url,
            user_code=device.user_code,
            device_auth_id=device.device_auth_id,
            interval_seconds=device.interval_seconds,
            expires_in_seconds=device.expires_in_seconds,
        )

    async def _poll_device_tokens(self, flow_id: str | None, context: "DevicePollContext") -> None:
        try:
            while time.time() < context.expires_at:
                route = await _oauth_route()
                tokens = await exchange_device_token(
                    device_auth_id=context.device_auth_id,
                    user_code=context.user_code,
                    route=route,
                    allow_direct_egress=route is None,
                )
                if tokens:
                    await self._persist_tokens(tokens, provider_name=OPENAI_PROVIDER_NAME)
                    await self._set_success(flow_id)
                    return
                await _async_sleep(context.interval_seconds)
            await self._set_error("Device code expired.", flow_id=flow_id)
        except OAuthError as exc:
            await self._set_error(exc.message, flow_id=flow_id)
        except AccountIdentityConflictError:
            await self._set_error(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE, flow_id=flow_id)
        finally:
            async with self._store.lock:
                flow = self._store.get_flow_locked(flow_id)
                current = asyncio.current_task()
                if flow is not None and flow.poll_task is current:
                    flow.poll_task = None
                    self._store.set_latest_flow_locked(flow)

    def _ensure_device_poll_task_locked(self, state: OAuthState) -> bool:
        if state.poll_task and not state.poll_task.done():
            return True
        if not state.device_auth_id or not state.user_code or not state.expires_at:
            return False

        interval = state.interval_seconds if state.interval_seconds is not None else 0
        poll_context = DevicePollContext(
            device_auth_id=state.device_auth_id,
            user_code=state.user_code,
            interval_seconds=max(interval, 0),
            expires_at=state.expires_at,
        )
        state.poll_task = asyncio.create_task(self._poll_device_tokens(state.flow_id, poll_context))
        return True

    async def _exchange_authorization_code(
        self,
        provider_name: str,
        *,
        code: str,
        code_verifier: str,
        state: str | None = None,
    ) -> OAuthTokens:
        provider = get_provider(provider_name)
        oauth_config = provider.oauth_config()
        if provider.name == ANTHROPIC_PROVIDER_NAME:
            return await exchange_anthropic_authorization_code(
                code=code,
                code_verifier=code_verifier,
                state=state,
                redirect_uri=oauth_config.redirect_uri,
                token_url=oauth_config.token_url,
                client_id=oauth_config.client_id,
            )
        route = await _oauth_route()
        return await exchange_authorization_code(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=oauth_config.redirect_uri,
            base_url=oauth_config.auth_base_url,
            client_id=oauth_config.client_id,
            route=route,
            allow_direct_egress=route is None,
        )

    async def _persist_tokens(self, tokens: OAuthTokens, *, provider_name: str = OPENAI_PROVIDER_NAME) -> None:
        account = account_from_tokens(tokens, provider_name, self._encryptor)
        raw_account_id = account.chatgpt_account_id
        workspace_id = account.workspace_id
        if self._repo_factory:
            async with self._repo_factory() as repo:
                if raw_account_id and workspace_id is None:
                    await repo.upsert(account, merge_by_email=False, merge_by_chatgpt_identity=True)
                else:
                    await repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)
        else:
            if raw_account_id and workspace_id is None:
                await self._accounts_repo.upsert(
                    account,
                    merge_by_email=False,
                    merge_by_chatgpt_identity=True,
                )
            else:
                await self._accounts_repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)

        await self._invalidate_account_routing_caches()

    async def _invalidate_account_routing_caches(self) -> None:
        get_account_selection_cache().invalidate()
        get_api_key_cache().clear()
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)

    async def _set_success(self, flow_id: str | None = None) -> None:
        async with self._store.lock:
            flow = self._store.get_flow_locked(flow_id)
            if flow_id is not None and flow is None:
                return
            if flow is None:
                self._store.state.status = "success"
                self._store.state.error_message = None
                return
            self._store.set_flow_status_locked(flow, status="success", error_message=None)

    async def _set_error(self, message: str, flow_id: str | None = None) -> None:
        await self._record_error(message, flow_id)

        asyncio.create_task(self._stop_callback_server_if_idle())

    async def _record_error(self, message: str, flow_id: str | None) -> None:
        async with self._store.lock:
            if flow_id is None and self._store.state.flow_id is not None:
                return
            flow = self._store.get_flow_locked(flow_id)
            if flow_id is not None and flow is None:
                return
            if flow is None:
                self._store.state.status = "error"
                self._store.state.error_message = message
                return
            self._store.set_flow_status_locked(flow, status="error", error_message=message)


@dataclass(frozen=True)
class DevicePollContext:
    device_auth_id: str
    user_code: str
    interval_seconds: int
    expires_at: float
