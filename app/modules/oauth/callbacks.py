from __future__ import annotations

import asyncio
import html
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from aiohttp import web

from app.core.clients.oauth import OAuthError, OAuthTokens
from app.modules.accounts.identity_repository import AccountIdentityConflictError
from app.modules.oauth.account_bind import ACCOUNT_IDENTITY_CONFLICT_MESSAGE
from app.modules.oauth.durable import DurableOAuthFlows
from app.modules.oauth.schemas import ManualCallbackResponse
from app.modules.oauth.state import OAuthCallbackServer, OAuthState, OAuthStateStore

logger = logging.getLogger("app.modules.oauth.service")
_SUCCESS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "oauth_success.html"


class OAuthCallbackHandlers(ABC):
    _flows: DurableOAuthFlows
    _store: OAuthStateStore

    @abstractmethod
    async def _exchange_authorization_code(
        self, provider_name: str, *, code: str, code_verifier: str, state: str | None = None
    ) -> OAuthTokens: ...

    @abstractmethod
    async def _persist_tokens(self, tokens: OAuthTokens, flow: OAuthState) -> int | None: ...

    async def manual_callback(self, callback_url: str, flow_id: str | None = None) -> ManualCallbackResponse:
        parsed = urlparse(callback_url)
        bare_code = not parsed.scheme and not any(mark in callback_url for mark in "#?=&")
        if bare_code:
            params = {"code": [callback_url]}
        elif not parsed.scheme and "#" in callback_url:
            code, state = callback_url.split("#", 1)
            params = {"code": [code], "state": [state]}
        else:
            params = parse_qs(parsed.query)
            if parsed.fragment:
                params.update(parse_qs(parsed.fragment))

        error = params.get("error", [None])[0]
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        return await self._complete_browser_flow(
            code, state, error, flow_id=flow_id, allow_bare_code=bare_code and flow_id is not None
        )

    async def _complete_browser_flow(
        self,
        code: str | None,
        state: str | None,
        error: str | None,
        *,
        flow_id: str | None = None,
        allow_bare_code: bool = False,
    ) -> ManualCallbackResponse:
        invalid = "Invalid OAuth callback: state mismatch or missing code."
        if not state and not allow_bare_code:
            return ManualCallbackResponse(status="error", error_message=_provider_error(error) if error else invalid)

        flow = await self._flows.get(flow_id, state=state, include_secrets=True)
        if flow is None or flow.method != "browser" or flow.flow_id is None:
            return ManualCallbackResponse(status="error", error_message=_provider_error(error) if error else invalid)
        if flow.status != "pending":
            return _callback_result(flow.status, flow.error_message, flow.probe_status_code)

        if error or not code or not flow.state_token or not flow.code_verifier:
            message = _provider_error(error) if error else invalid
            if not await self._flows.finish(flow.flow_id, from_status="pending", error=message):
                return await self._browser_result(flow.flow_id)
            await self._forget_browser_flow(flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=message)
        if not await self._flows.claim(flow.flow_id):
            return await self._browser_result(flow.flow_id)

        probe_status_code = None
        try:
            tokens = await self._exchange_authorization_code(
                flow.provider, code=code, code_verifier=flow.code_verifier, state=flow.state_token
            )
            probe_status_code = await self._persist_tokens(tokens, flow)
            message = None
        except OAuthError as exc:
            message = exc.message
        except AccountIdentityConflictError:
            message = ACCOUNT_IDENTITY_CONFLICT_MESSAGE
        except Exception as exc:
            logger.error("OAuth callback failed exception_type=%s", type(exc).__name__)
            message = "An internal error occurred."
        await self._flows.finish(
            flow.flow_id,
            from_status="verifying" if probe_status_code is not None else "processing",
            error=message,
            probe_status_code=probe_status_code,
        )
        await self._forget_browser_flow(flow.flow_id)
        return await self._browser_result(flow.flow_id)

    async def _browser_result(self, flow_id: str) -> ManualCallbackResponse:
        flow = await self._flows.get(flow_id)
        if flow is None:
            return ManualCallbackResponse(status="error", error_message="This sign-in is no longer available.")
        return _callback_result(flow.status, flow.error_message, flow.probe_status_code)

    async def _forget_browser_flow(self, flow_id: str) -> None:
        async with self._store.lock:
            tracked = self._store.get_flow_locked(flow_id)
            if tracked is not None:
                self._store.remove_flow_locked(tracked)
            if timer := self._store.callback_expiries.pop(flow_id, None):
                timer.cancel()
        asyncio.create_task(self._stop_callback_server_if_idle())

    async def _handle_callback(self, request: web.Request) -> web.Response:
        params = request.rel_url.query
        result = await self._complete_browser_flow(params.get("code"), params.get("state"), params.get("error"))
        if result.status == "success":
            return self._html_response(_success_html())
        if result.status == "pending":
            return self._html_response(
                "<html><body><h1>Sign-in in progress</h1><p>Check the dashboard.</p></body></html>"
            )
        return self._html_response(_error_html(result.error_message or "Authorization failed."))

    def _start_callback_server_stop_locked(self, server: OAuthCallbackServer) -> asyncio.Task[None]:
        stop_task = self._store._callback_server_stop_task
        if stop_task is not None and not stop_task.done():
            return stop_task
        stop_task = asyncio.create_task(server.stop())
        self._store._callback_server_stop_task = stop_task
        return stop_task

    async def _finish_callback_server_stop(
        self,
        server: OAuthCallbackServer,
        stop_task: asyncio.Task[None],
    ) -> None:
        try:
            await asyncio.shield(stop_task)
        finally:
            async with self._store.lock:
                if self._store._callback_server is server:
                    self._store._callback_server = None
                    self._store._callback_server_bound_at = None
                if self._store._callback_server_stop_task is stop_task:
                    self._store._callback_server_stop_task = None

    async def _wait_for_callback_server_stop(self) -> None:
        while True:
            async with self._store.lock:
                stop_task = self._store._callback_server_stop_task
            if stop_task is None:
                return
            await asyncio.shield(stop_task)

    async def _stop_callback_server_if_idle(self) -> None:
        server: OAuthCallbackServer | None = None
        stop_task: asyncio.Task[None] | None = None
        async with self._store.lock:
            if self._store.has_pending_browser_flows_locked():
                return
            server = self._store._callback_server
            if server:
                stop_task = self._start_callback_server_stop_locked(server)
        if server and stop_task:
            await self._finish_callback_server_stop(server, stop_task)

    @staticmethod
    def _html_response(html: str) -> web.Response:
        return web.Response(text=html, content_type="text/html")


def _callback_result(status: str, message: str | None, probe_status_code: int | None) -> ManualCallbackResponse:
    if status in {"processing", "verifying"}:
        return ManualCallbackResponse(
            status="pending", error_message="Sign-in is already in progress. Check its status."
        )
    return ManualCallbackResponse(status=status, error_message=message, probe_status_code=probe_status_code)


def _provider_error(error: str) -> str:
    if error.isascii() and len(error) <= 64 and error.replace("_", "").isalnum():
        return f"OAuth error: {error}"
    return "Authorization failed."


def _success_html() -> str:
    try:
        return _SUCCESS_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return "<html><body><h1>Login complete</h1><p>Return to the dashboard.</p></body></html>"


def _error_html(message: str) -> str:
    escaped_message = html.escape(message, quote=True)
    return f"<html><body><h1>Login failed</h1><p>{escaped_message}</p></body></html>"
