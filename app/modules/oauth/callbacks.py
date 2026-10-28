from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from app.core.clients.oauth import OAuthError
from app.core.providers import get_provider
from app.modules.accounts.identity_repository import AccountIdentityConflictError
from app.modules.oauth.schemas import ManualCallbackResponse
from app.modules.oauth.state import OAuthCallbackServer

if TYPE_CHECKING:
    from app.modules.oauth.service import OauthService

logger = logging.getLogger("app.modules.oauth.service")
_SUCCESS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "oauth_success.html"
_ACCOUNT_IDENTITY_CONFLICT_MESSAGE = (
    "Multiple accounts match the authenticated identity. Remove duplicate accounts and retry OAuth."
)


class OAuthCallbackHandlers:
    async def manual_callback(
        self: OauthService, callback_url: str, flow_id: str | None = None
    ) -> ManualCallbackResponse:

        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(callback_url)
        if not parsed.scheme and "#" in callback_url:
            code, state = callback_url.split("#", 1)
            params = {"code": [code], "state": [state]}
        else:
            params = parse_qs(parsed.query)
            if parsed.fragment:
                params.update(parse_qs(parsed.fragment))

        error = params.get("error", [None])[0]
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        async with self._store.lock:
            flow = self._store.get_flow_by_state_token_locked(state)
            verifier = flow.code_verifier if flow is not None else None
            target_flow_id = flow.flow_id if flow is not None else flow_id
            can_update_error = target_flow_id is not None
            if flow_id is not None and (flow is None or flow.flow_id != flow_id):
                flow = None
                verifier = None
                target_flow_id = None
                can_update_error = False
            if flow is not None and flow.status == "success" and state == flow.state_token:
                return ManualCallbackResponse(status="success")

        if error:
            message = f"OAuth error: {error}"
            if can_update_error:
                await self._set_error(message, flow_id=target_flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

        if not code or not state or flow is None or not verifier:
            message = "Invalid OAuth callback: state mismatch or missing code."
            if can_update_error:
                await self._set_error(message, flow_id=target_flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

        try:
            provider = get_provider(flow.provider)
            tokens = await self._exchange_authorization_code(
                provider.name, code=code, code_verifier=verifier, state=state
            )
            await self._persist_tokens(tokens, provider_name=provider.name, flow_id=flow.flow_id)
            await self._set_success(flow.flow_id)
            asyncio.create_task(self._stop_callback_server_if_idle())
            return ManualCallbackResponse(status="success")
        except OAuthError as exc:
            await self._set_error(exc.message, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=exc.message)
        except AccountIdentityConflictError:
            await self._set_error(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=_ACCOUNT_IDENTITY_CONFLICT_MESSAGE)
        except Exception as exc:
            logger.error("manual OAuth callback failed exception_type=%s", type(exc).__name__)
            message = "An internal error occurred."
            await self._set_error(message, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

    async def _handle_callback(self: OauthService, request: web.Request) -> web.Response:
        params = request.rel_url.query
        error = params.get("error")
        code = params.get("code")
        state = params.get("state")

        async with self._store.lock:
            flow = self._store.get_flow_by_state_token_locked(state)
            verifier = flow.code_verifier if flow is not None else None
            if flow is not None and flow.status == "success":
                return self._html_response(_success_html())

        if error:
            await self._set_error(f"OAuth error: {error}", flow_id=flow.flow_id if flow is not None else None)
            return self._html_response(_error_html("Authorization failed."))

        if not code or not state or flow is None or not verifier:
            await self._set_error("Invalid OAuth callback state.", flow_id=flow.flow_id if flow is not None else None)
            if flow is None:
                logger.warning("OAuth callback for a sign-in this process did not start")
                return self._html_response(
                    _error_html(
                        "This sign-in was not started by the agent-lb instance that received it "
                        "(it may have restarted). Start the sign-in again from the dashboard."
                    )
                )
            return self._html_response(_error_html("Invalid OAuth callback."))

        try:
            provider = get_provider(flow.provider)
            tokens = await self._exchange_authorization_code(
                provider.name, code=code, code_verifier=verifier, state=state
            )
            await self._persist_tokens(tokens, provider_name=provider.name, flow_id=flow.flow_id)
            await self._set_success(flow.flow_id)
            html = _success_html()
        except OAuthError as exc:
            await self._set_error(exc.message, flow_id=flow.flow_id)
            html = _error_html(exc.message)
        except AccountIdentityConflictError:
            await self._set_error(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE, flow_id=flow.flow_id)
            html = _error_html(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE)

        asyncio.create_task(self._stop_callback_server_if_idle())
        return self._html_response(html)

    def _start_callback_server_stop_locked(self: OauthService, server: OAuthCallbackServer) -> asyncio.Task[None]:
        stop_task = self._store._callback_server_stop_task
        if stop_task is not None and not stop_task.done():
            return stop_task
        stop_task = asyncio.create_task(server.stop())
        self._store._callback_server_stop_task = stop_task
        return stop_task

    async def _finish_callback_server_stop(
        self: OauthService,
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

    async def _wait_for_callback_server_stop(self: OauthService) -> None:
        while True:
            async with self._store.lock:
                stop_task = self._store._callback_server_stop_task
            if stop_task is None:
                return
            await asyncio.shield(stop_task)

    async def _stop_callback_server_if_idle(self: OauthService) -> None:
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


def _success_html() -> str:
    try:
        return _SUCCESS_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return "<html><body><h1>Login complete</h1><p>Return to the dashboard.</p></body></html>"


def _error_html(message: str) -> str:
    escaped_message = html.escape(message, quote=True)
    return f"<html><body><h1>Login failed</h1><p>{escaped_message}</p></body></html>"
