from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import AbstractAsyncContextManager
from typing import Callable

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.clients.oauth import (
    OAuthError,
    OAuthTokens,
    build_authorization_url,
    exchange_authorization_code,
    generate_pkce_pair,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import (
    GEMINI_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    ProviderLookupError,
    get_provider,
)
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.gemini_pool_scope import attach_gemini_pool_to_scoped_keys
from app.modules.oauth.account_bind import load_expected_account, requires_bound_probe, save_signed_in_account
from app.modules.oauth.callbacks import OAuthCallbackHandlers
from app.modules.oauth.callbacks import (
    _error_html as _error_html,
)
from app.modules.oauth.device import DeviceOAuthHandlers
from app.modules.oauth.durable import POST_SAVE_TIMEOUT_SECONDS, DurableOAuthFlows
from app.modules.oauth.provider_flows import PROVIDER_OAUTH_FLOWS
from app.modules.oauth.schemas import (
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)
from app.modules.oauth.state import (
    OAUTH_FLOW_TTL_SECONDS,
    ExpectedAccount,
    OAuthCallbackServer,
    OAuthState,
    OAuthStateStore,
)
from app.modules.oauth.token_account import account_from_tokens
from app.modules.proxy.account_cache import get_account_selection_cache

logger = logging.getLogger(__name__)
_CALLBACK_PORT_RELEASE_GRACE_SECONDS = 1.0
_PRUNE_INTERVAL_SECONDS = 60


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
_prune_task: asyncio.Task[None] | None = None
_stop_prune: asyncio.Event | None = None


async def _prune_oauth_flows(flows: DurableOAuthFlows, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=_PRUNE_INTERVAL_SECONDS)
            return
        except TimeoutError:
            pass
        try:
            await flows.prune()
        except Exception as exc:
            logger.error("OAuth cleanup failed exception_type=%s", type(exc).__name__)


async def resume_oauth_flows() -> None:
    global _prune_task, _stop_prune
    if get_settings().federation_peer_url:
        return
    async with get_background_session() as session:
        service = OauthService(AccountsRepository(session))
        await service._flows.prune()
        browser_flow_id = None
        for flow in await service._flows.active():
            if flow.method == "device":
                await service._ensure_device_poll_task(flow)
            else:
                async with _OAUTH_STORE.lock:
                    _OAUTH_STORE.remember_flow_locked(flow)
                service._schedule_callback_listener_expiry(flow)
                browser_flow_id = flow.flow_id
        if browser_flow_id is not None:
            await service._ensure_callback_server(browser_flow_id)
        if _prune_task is None or _prune_task.done():
            _stop_prune = asyncio.Event()
            _prune_task = asyncio.create_task(_prune_oauth_flows(service._flows, _stop_prune))


async def stop_oauth_flows() -> None:
    global _prune_task, _stop_prune
    if _prune_task is not None and _stop_prune is not None:
        _stop_prune.set()
        await asyncio.gather(_prune_task, return_exceptions=True)
        _prune_task = None
        _stop_prune = None
    await _OAUTH_STORE.reset()


async def pending_oauth_flow_ages() -> list[float]:
    async with _OAUTH_STORE.lock:
        return _OAUTH_STORE.pending_flow_ages_locked()


class OauthService(OAuthCallbackHandlers, DeviceOAuthHandlers):
    def __init__(
        self,
        accounts_repo: AccountsRepository,
        repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepository]] | None = None,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._encryptor = TokenEncryptor()
        self._flows = DurableOAuthFlows(self._encryptor)
        self._store = _OAUTH_STORE
        self._repo_factory = repo_factory

    async def start_oauth(self, request: OauthStartRequest) -> OauthStartResponse:
        force_method = (request.force_method or "").lower()
        try:
            provider = get_provider(request.provider)
        except ProviderLookupError as exc:
            raise OAuthError("unsupported_provider", str(exc), 400) from exc
        expected = (
            await load_expected_account(self._accounts_repo, request.expected_account_id, provider.name)
            if request.expected_account_id is not None
            else None
        )
        if not force_method and expected is None:
            accounts = await self._accounts_repo.list_accounts()
            has_openai_account = any(
                getattr(account, "provider", OPENAI_PROVIDER_NAME) == OPENAI_PROVIDER_NAME for account in accounts
            )
            if provider.name == OPENAI_PROVIDER_NAME and has_openai_account:
                server: OAuthCallbackServer | None = None
                stop_task: asyncio.Task[None] | None = None
                async with self._store.lock:
                    self._store.compat_success = True
                    if not self._store.has_pending_browser_flows_locked():
                        server = self._store._callback_server
                    if server:
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
            return await self._start_device_flow(request, expected)

        try:
            return await self._start_browser_flow(
                provider.name,
                request,
                expected,
            )
        except OSError:
            if provider.name != OPENAI_PROVIDER_NAME:
                raise
            return await self._start_device_flow(request, expected)

    async def oauth_status(self, flow_id: str | None = None) -> OauthStatusResponse:
        async with self._store.lock:
            if flow_id is None and self._store.compat_success:
                return OauthStatusResponse(status="success")
        flow = await self._flows.get(flow_id)
        if flow is None:
            return OauthStatusResponse(status="pending")
        return OauthStatusResponse(
            status="pending" if flow.status in {"processing", "verifying"} else flow.status,
            error_message=flow.error_message,
            probe_status_code=flow.probe_status_code,
        )

    async def complete_oauth(self, request: OauthCompleteRequest | None = None) -> OauthCompleteResponse:
        payload = request or OauthCompleteRequest()
        flow = await self._flows.get(payload.flow_id)
        if flow is not None and flow.status == "pending" and flow.method == "device":
            await self._ensure_device_poll_task(flow)
        if flow is not None:
            return OauthCompleteResponse(
                status="pending" if flow.status in {"processing", "verifying"} else flow.status
            )
        return OauthCompleteResponse(status=(await self.oauth_status(payload.flow_id)).status)

    async def _start_browser_flow(
        self,
        provider_name: str,
        request: OauthStartRequest,
        expected: ExpectedAccount | None,
    ) -> OauthStartResponse:
        flow_id = secrets.token_urlsafe(12)
        code_verifier, code_challenge = generate_pkce_pair()
        provider = get_provider(provider_name)
        flow = PROVIDER_OAUTH_FLOWS.get(provider.name)
        state_token = secrets.token_urlsafe(flow.state_token_bytes if flow is not None else 16)
        oauth_config = provider.oauth_config()
        if flow is not None:
            authorization_url = flow.authorization_url(oauth_config, state=state_token, code_challenge=code_challenge)
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
        started = time.time()
        pending = OAuthState(
            flow_id=flow_id,
            method="browser",
            provider=provider.name,
            state_token=state_token,
            code_verifier=code_verifier,
            started_at=started,
            expires_at=started + OAUTH_FLOW_TTL_SECONDS,
            warmup_hours=request.warmup_hours,
            warmup_capacity_percent=request.warmup_capacity_percent,
            warmup_max_concurrent=request.warmup_max_concurrent,
            expected_account=expected,
        )
        await self._flows.insert(pending)
        async with self._store.lock:
            self._store.remember_flow_locked(pending)
            self._schedule_callback_listener_expiry(pending)

        bind_task = asyncio.ensure_future(self._ensure_callback_server(flow_id))
        self._store._callback_server_bind_tasks.add(bind_task)
        bind_task.add_done_callback(self._store._callback_server_bind_tasks.discard)
        await asyncio.shield(bind_task)

        return OauthStartResponse(
            flow_id=flow_id,
            method="browser",
            authorization_url=authorization_url,
            callback_url=oauth_config.redirect_uri,
            expected_account_id=request.expected_account_id,
        )

    def _schedule_callback_listener_expiry(self, flow: OAuthState) -> None:
        assert flow.expires_at is not None and flow.flow_id is not None
        if timer := self._store.callback_expiries.get(flow.flow_id):
            timer.cancel()
        self._store.callback_expiries[flow.flow_id] = asyncio.get_running_loop().call_later(
            max(0, flow.expires_at - time.time()) + _CALLBACK_PORT_RELEASE_GRACE_SECONDS,
            self._expire_callback_listener,
            flow.flow_id,
        )

    def _expire_callback_listener(self, flow_id: str) -> None:
        self._store.callback_expiries.pop(flow_id, None)
        asyncio.create_task(self._stop_callback_server_if_idle())

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
                    await self._flows.finish(
                        flow_id, from_status="pending", error="Could not open the OAuth callback listener."
                    )
                    await self._forget_browser_flow(flow_id)
                    raise
                else:
                    async with self._store.lock:
                        self._store._callback_server = callback_server
                        self._store._callback_server_bound_at = time.time()
        if callback_server is not None:
            await self._stop_callback_server_if_idle()

    _oauth_route = staticmethod(_oauth_route)

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
        flow = PROVIDER_OAUTH_FLOWS.get(provider.name)
        if flow is not None:
            return await flow.exchange_code(oauth_config, code=code, code_verifier=code_verifier, state=state)
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

    async def _persist_tokens(self, tokens: OAuthTokens, flow: OAuthState) -> int | None:
        assert flow.flow_id is not None
        if flow.expires_at is None or flow.expires_at <= time.time():
            raise OAuthError("expired_flow", "This sign-in has expired; nothing was saved.", 409)
        account = account_from_tokens(tokens, flow.provider, self._encryptor, flow=flow)
        if self._repo_factory:
            async with self._repo_factory() as repo:
                await save_signed_in_account(repo, account, flow.expected_account, flow_id=flow.flow_id)
        else:
            async with get_background_session() as session:
                await save_signed_in_account(
                    AccountsRepository(session), account, flow.expected_account, flow_id=flow.flow_id
                )

        probe_status_code = None
        if flow.expected_account is not None and requires_bound_probe(flow.provider):
            probe_status_code = await _verify_committed_account(flow.expected_account.account_id)
        try:
            async with asyncio.timeout(5):
                if flow.provider == GEMINI_PROVIDER_NAME:
                    await attach_gemini_pool_to_scoped_keys()
                await self._invalidate_account_routing_caches()
        except Exception as exc:
            logger.error("OAuth post-save cache update failed exception_type=%s", type(exc).__name__)
        return probe_status_code

    async def _invalidate_account_routing_caches(self) -> None:
        get_account_selection_cache().invalidate()
        get_api_key_cache().clear()
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)


async def _verify_committed_account(account_id: str) -> int:
    try:
        from app.modules.accounts.reauth_completion import verify_bound_reauth

        async with asyncio.timeout(POST_SAVE_TIMEOUT_SECONDS):
            return await verify_bound_reauth(account_id)
    except Exception as exc:
        logger.error("OAuth post-save verification failed exception_type=%s", type(exc).__name__)
        return 0
