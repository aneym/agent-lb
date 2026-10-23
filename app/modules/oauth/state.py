from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiohttp import web

from app.core.providers import OPENAI_PROVIDER_NAME

_TERMINAL_OAUTH_STATUSES = {"error", "success"}
_MAX_RETAINED_TERMINAL_OAUTH_FLOWS = 16


@dataclass
class OAuthState:
    flow_id: str | None = None
    status: str = "pending"
    method: str | None = None
    provider: str = OPENAI_PROVIDER_NAME
    error_message: str | None = None
    state_token: str | None = None
    intended_account_id: str | None = None
    code_verifier: str | None = None
    device_auth_id: str | None = None
    user_code: str | None = None
    interval_seconds: int | None = None
    expires_at: float | None = None
    finished_at: float | None = None
    started_at: float | None = None
    callback_server: "OAuthCallbackServer | None" = None
    poll_task: asyncio.Task[None] | None = None


class OAuthStateStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = OAuthState(status="idle")
        self._flows: dict[str, OAuthState] = {}
        self._state_token_index: dict[str, str] = {}
        self._callback_server: OAuthCallbackServer | None = None
        self._callback_server_stop_task: asyncio.Task[None] | None = None
        self._callback_server_start_lock = asyncio.Lock()
        self._callback_server_bound_at: float | None = None
        self._callback_server_bind_tasks: set[asyncio.Task[None]] = set()

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def state(self) -> OAuthState:
        return self._state

    def get_flow_locked(self, flow_id: str | None) -> OAuthState | None:
        resolved_flow_id = flow_id or self._state.flow_id
        if resolved_flow_id is None:
            return None
        return self._flows.get(resolved_flow_id)

    def get_flow_by_state_token_locked(self, state_token: str | None) -> OAuthState | None:
        if state_token is None:
            return None
        flow_id = self._state_token_index.get(state_token)
        if flow_id is None:
            return None
        return self._flows.get(flow_id)

    def remember_flow_locked(self, flow: OAuthState) -> None:
        if flow.flow_id is None:
            raise ValueError("flow_id is required")
        self.prune_expired_pending_browser_flows_locked()
        self._flows[flow.flow_id] = flow
        if flow.state_token is not None:
            self._state_token_index[flow.state_token] = flow.flow_id
        self.set_latest_flow_locked(flow)

    def set_latest_flow_locked(self, flow: OAuthState) -> None:
        self._state = OAuthState(
            flow_id=flow.flow_id,
            status=flow.status,
            method=flow.method,
            provider=flow.provider,
            intended_account_id=flow.intended_account_id,
            error_message=flow.error_message,
            state_token=flow.state_token,
            code_verifier=flow.code_verifier,
            device_auth_id=flow.device_auth_id,
            user_code=flow.user_code,
            interval_seconds=flow.interval_seconds,
            expires_at=flow.expires_at,
            finished_at=flow.finished_at,
            started_at=flow.started_at,
            poll_task=flow.poll_task,
        )

    def set_flow_status_locked(self, flow: OAuthState, *, status: str, error_message: str | None) -> None:
        flow.status = status
        flow.error_message = error_message
        flow.finished_at = time.time() if status in _TERMINAL_OAUTH_STATUSES else None
        self.set_latest_flow_locked(flow)
        if status in _TERMINAL_OAUTH_STATUSES:
            self.prune_terminal_flows_locked()

    def pending_flow_ages_locked(self) -> list[float]:
        self.prune_expired_pending_browser_flows_locked()
        now = time.time()
        ages = sorted(
            (
                round(now - (flow.started_at or now), 1)
                for flow in self._flows.values()
                if flow.status == "pending" and (flow.expires_at is None or flow.expires_at > now)
            )
        )
        if not ages and (self._callback_server is not None or self._callback_server_stop_task is not None):
            ages = [round(now - (self._callback_server_bound_at or now), 1)]
        return ages

    def has_pending_browser_flows_locked(self) -> bool:
        self.prune_expired_pending_browser_flows_locked()
        return any((flow.method == "browser" and flow.status == "pending" for flow in self._flows.values()))

    async def reset(self) -> None:
        server: OAuthCallbackServer | None = None
        async with self._lock:
            server = self._cleanup_locked()
            self._state = OAuthState(status="idle")
        if server is not None:
            await server.stop()

    def _cleanup_locked(self, *, clear_callback_server: bool = True) -> OAuthCallbackServer | None:
        for flow in self._flows.values():
            task = flow.poll_task
            if task and (not task.done()):
                task.cancel()
        server = self._callback_server
        if clear_callback_server:
            self._callback_server = None
            self._callback_server_bound_at = None
        self._flows.clear()
        self._state_token_index.clear()
        return server

    def prune_terminal_flows_locked(self) -> None:
        terminal_flows = [
            flow
            for flow in self._flows.values()
            if flow.flow_id is not None and flow.status in _TERMINAL_OAUTH_STATUSES
        ]
        extra_count = len(terminal_flows) - _MAX_RETAINED_TERMINAL_OAUTH_FLOWS
        if extra_count <= 0:
            return
        terminal_flows.sort(key=lambda flow: flow.finished_at or 0)
        for flow in terminal_flows[:extra_count]:
            self.remove_flow_locked(flow)

    def prune_expired_pending_browser_flows_locked(self) -> None:
        now = time.time()
        expired_flows = [
            flow
            for flow in self._flows.values()
            if flow.method == "browser"
            and flow.status == "pending"
            and (flow.expires_at is not None)
            and (flow.expires_at <= now)
        ]
        for flow in expired_flows:
            self.remove_flow_locked(flow)

    def remove_pending_device_flows_locked(self) -> None:
        pending_device_flows = [
            flow for flow in self._flows.values() if flow.method == "device" and flow.status == "pending"
        ]
        for flow in pending_device_flows:
            task = flow.poll_task
            if task and (not task.done()):
                task.cancel()
            self.remove_flow_locked(flow)

    def remove_flow_locked(self, flow: OAuthState) -> None:
        removed_latest = flow.flow_id is not None and flow.flow_id == self._state.flow_id
        if flow.flow_id is not None:
            self._flows.pop(flow.flow_id, None)
        if flow.state_token is not None:
            self._state_token_index.pop(flow.state_token, None)
        if removed_latest:
            self._restore_latest_flow_locked()

    def _restore_latest_flow_locked(self) -> None:
        if not self._flows:
            self._state = OAuthState(status="idle")
            return
        latest_flow = max(self._flows.values(), key=lambda flow: flow.finished_at or flow.expires_at or 0)
        self.set_latest_flow_locked(latest_flow)


class OAuthCallbackServer:
    def __init__(
        self, handler: Callable[[web.Request], Awaitable[web.StreamResponse]], host: str = "127.0.0.1", port: int = 1455
    ) -> None:
        self._handler = handler
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/auth/callback", self._handler)
        app.router.add_get("/callback", self._handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
