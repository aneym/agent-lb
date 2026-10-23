from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import AbstractAsyncContextManager
from typing import Callable

from app.core.anthropic.oauth import build_anthropic_authorization_url, exchange_anthropic_authorization_code
from app.core.clients.oauth import (
    OAuthError,
    OAuthTokens,
    build_authorization_url,
    exchange_authorization_code,
    exchange_device_token,
    generate_pkce_pair,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME, ProviderLookupError, get_provider
from app.modules.accounts.identity_repository import AccountIdentityConflictError
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth.account_persistence import OAuthAccountPersistence as OAuthAccountPersistence
from app.modules.oauth.callbacks import (
    _ACCOUNT_IDENTITY_CONFLICT_MESSAGE,
    OAuthCallbackHandlers,
    ReauthSeatMismatchError,
)
from app.modules.oauth.callbacks import _error_html as _error_html
from app.modules.oauth.device_flows import DevicePollContext as DevicePollContext
from app.modules.oauth.device_flows import OAuthDeviceFlows as OAuthDeviceFlows
from app.modules.oauth.durable_flows import OAuthDurableFlows as OAuthDurableFlows
from app.modules.oauth.flow_constants import (
    _PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS as _PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS,
)
from app.modules.oauth.flow_constants import _REAUTH_SEAT_MISMATCH_MESSAGE as _REAUTH_SEAT_MISMATCH_MESSAGE
from app.modules.oauth.flow_constants import _TERMINAL_OAUTH_STATUSES as _TERMINAL_OAUTH_STATUSES
from app.modules.oauth.repository import OAuthFlowRecord, epoch_to_naive_utc
from app.modules.oauth.route import _has_active_proxy_bindings as _has_active_proxy_bindings
from app.modules.oauth.route import _oauth_route as _oauth_route
from app.modules.oauth.schemas import (
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)
from app.modules.oauth.state import _MAX_RETAINED_TERMINAL_OAUTH_FLOWS as _MAX_RETAINED_TERMINAL_OAUTH_FLOWS
from app.modules.oauth.state import OAuthCallbackServer, OAuthState, OAuthStateStore

_async_sleep = asyncio.sleep
logger = logging.getLogger(__name__)
_CALLBACK_PORT_RELEASE_GRACE_SECONDS = 1.0
_OAUTH_STORE = OAuthStateStore()


async def pending_oauth_flow_ages() -> list[float]:
    async with _OAUTH_STORE.lock:
        return _OAUTH_STORE.pending_flow_ages_locked()


class OauthService(OAuthDeviceFlows, OAuthAccountPersistence, OAuthDurableFlows, OAuthCallbackHandlers):
    def __init__(
        self,
        accounts_repo: AccountsRepository,
        repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepository]] | None = None,
        store: OAuthStateStore | None = None,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._encryptor = TokenEncryptor()
        self._store = store if store is not None else _OAUTH_STORE
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
                (getattr(account, "provider", OPENAI_PROVIDER_NAME) == OPENAI_PROVIDER_NAME for account in accounts)
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
                    "unsupported_provider_flow", "Device login is only supported for OpenAI accounts.", 400
                )
            return await self._start_device_flow(intended_account_id=request.account_id)
        try:
            if provider.name == OPENAI_PROVIDER_NAME:
                return await self._start_browser_flow(intended_account_id=request.account_id)
            return await self._start_browser_flow(provider.name, intended_account_id=request.account_id)
        except OSError:
            if provider.name != OPENAI_PROVIDER_NAME:
                raise
            return await self._start_device_flow(intended_account_id=request.account_id)

    async def oauth_status(self, flow_id: str | None = None) -> OauthStatusResponse:
        if flow_id is not None:
            record = await self._reconcile_flow_from_durable(flow_id=flow_id)
            if record is not None:
                status = record.status if record.status != "idle" else "pending"
                return OauthStatusResponse(status=status, error_message=record.error_message)
        async with self._store.lock:
            state = self._store.get_flow_locked(flow_id)
            if state is None:
                state = self._store.state if flow_id is None else OAuthState(status="pending")
            status = state.status if state.status != "idle" else "pending"
            return OauthStatusResponse(status=status, error_message=state.error_message)

    async def complete_oauth(self, request: OauthCompleteRequest | None = None) -> OauthCompleteResponse:
        payload = request or OauthCompleteRequest()
        if payload.flow_id is not None:
            record = await self._reconcile_flow_from_durable(flow_id=payload.flow_id)
            if record is not None and record.status in _TERMINAL_OAUTH_STATUSES:
                return OauthCompleteResponse(status=record.status)
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
            if state.method == "device":
                if state.status in _TERMINAL_OAUTH_STATUSES and payload.flow_id is not None:
                    return OauthCompleteResponse(status=state.status)
                return OauthCompleteResponse(status="pending")
            if state.status == "success":
                return OauthCompleteResponse(status="success")
            return OauthCompleteResponse(status="pending")

    async def _start_browser_flow(
        self, provider_name: str = OPENAI_PROVIDER_NAME, *, intended_account_id: str | None = None
    ) -> OauthStartResponse:
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
                    intended_account_id=intended_account_id,
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
        await self._persist_flow_record(
            OAuthFlowRecord(
                flow_id=flow_id,
                method="browser",
                status="pending",
                provider=provider.name,
                state_token=state_token,
                code_verifier=code_verifier,
                intended_account_id=intended_account_id,
                expires_at=epoch_to_naive_utc(time.time() + _PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS),
            )
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
                            self._handle_callback, host=settings.oauth_callback_host, port=settings.oauth_callback_port
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

    async def _poll_device_tokens(self, flow_id: str | None, context: "DevicePollContext") -> None:
        consumed = False
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
                    consumed = await self._consume_device_slot(flow_id)
                    if not consumed:
                        return
                    await self._persist_tokens(tokens, intended_account_id=context.intended_account_id)
                    await self._set_success(flow_id)
                    return
                await _async_sleep(context.interval_seconds)
            if consumed or await self._consume_device_slot(flow_id):
                await self._set_error("Device code expired.", flow_id=flow_id)
        except OAuthError as exc:
            if consumed or await self._consume_device_slot(flow_id):
                await self._set_error(exc.message, flow_id=flow_id)
        except ReauthSeatMismatchError:
            if consumed or await self._consume_device_slot(flow_id):
                await self._set_error(_REAUTH_SEAT_MISMATCH_MESSAGE, flow_id=flow_id)
        except AccountIdentityConflictError:
            if consumed or await self._consume_device_slot(flow_id):
                await self._set_error(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE, flow_id=flow_id)
        finally:
            async with self._store.lock:
                flow = self._store.get_flow_locked(flow_id)
                current = asyncio.current_task()
                if flow is not None and flow.poll_task is current:
                    flow.poll_task = None
                    self._store.set_latest_flow_locked(flow)

    async def _exchange_authorization_code(
        self, provider_name: str, *, code: str, code_verifier: str, state: str | None = None
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

    async def _set_success(self, flow_id: str | None = None) -> None:
        async with self._store.lock:
            flow = self._store.get_flow_locked(flow_id)
            if flow is not None:
                self._store.set_flow_status_locked(flow, status="success", error_message=None)
            elif flow_id is None:
                self._store.state.status = "success"
                self._store.state.error_message = None
        if flow_id is not None:
            await self._persist_flow_status(flow_id, status="success", error_message=None)

    async def _set_error(self, message: str, flow_id: str | None = None) -> bool:
        """Record a terminal error. Returns whether the durable error was applied.

        The durable write happens FIRST: if the monotonic guard rejects it
        (``False``) because the durable row is already ``success`` — a racing
        callback/poller committed success for the same single-use code — the
        local in-memory flow is left untouched (never shadowed into ``error``),
        and the caller must honor the durable success instead of surfacing the
        error. ``flow_id`` of ``None`` updates only the local latest state.
        """
        if flow_id is None:
            async with self._store.lock:
                if self._store.state.flow_id is None:
                    self._store.state.status = "error"
                    self._store.state.error_message = message
            return True
        applied = await self._persist_flow_status(flow_id, status="error", error_message=message)
        if not applied:
            return False
        async with self._store.lock:
            flow = self._store.get_flow_locked(flow_id)
            if flow is not None:
                self._store.set_flow_status_locked(flow, status="error", error_message=message)
        asyncio.create_task(self._stop_callback_server_if_idle())
        return True

    async def _finalize_callback_error(self, message: str, *, flow_id: str | None) -> str:
        if await self._set_error(message, flow_id=flow_id):
            return "error"
        record = await self._reconcile_flow_from_durable(flow_id=flow_id) if flow_id is not None else None
        if record is not None and record.status == "success":
            return "success"
        return "error"

    async def _record_error(self, message: str, flow_id: str | None) -> None:
        async with self._store.lock:
            if flow_id is None and self._store.state.flow_id is not None:
                return
            flow = self._store.get_flow_locked(flow_id)
            if flow_id is not None and flow is None:
                return
            if flow is None:
                if self._store.state.status == "success":
                    return
                self._store.state.status = "error"
                self._store.state.error_message = message
                return
            if flow.status == "success":
                return
            self._store.set_flow_status_locked(flow, status="error", error_message=message)
