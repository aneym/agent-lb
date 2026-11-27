from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiohttp import web

from app.core.providers import OPENAI_PROVIDER_NAME

OAUTH_FLOW_TTL_SECONDS = 60 * 60


@dataclass(frozen=True)
class ExpectedAccount:
    account_id: str
    email: str
    provider: str
    upstream_id: str | None
    workspace_id: str | None
    created_at: str


@dataclass
class OAuthState:
    flow_id: str | None = None
    status: str = "pending"
    method: str | None = None
    provider: str = OPENAI_PROVIDER_NAME
    error_message: str | None = None
    probe_status_code: int | None = None
    state_token: str | None = None
    code_verifier: str | None = None
    device_auth_id: str | None = None
    user_code: str | None = None
    interval_seconds: int | None = None
    expires_at: float | None = None
    started_at: float | None = None
    warmup_hours: float | None = None
    warmup_capacity_percent: float | None = None
    warmup_max_concurrent: int | None = None
    expected_account: ExpectedAccount | None = None
    poll_after: float | None = None
    poll_task: asyncio.Task[None] | None = None
    stop_poll: asyncio.Event | None = None


class OAuthStateStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.compat_success = False
        self._flows: dict[str, OAuthState] = {}
        self._callback_server: OAuthCallbackServer | None = None
        self._callback_server_stop_task: asyncio.Task[None] | None = None

        self._callback_server_start_lock = asyncio.Lock()
        self._callback_server_bound_at: float | None = None

        self._callback_server_bind_tasks: set[asyncio.Task[None]] = set()
        self.callback_expiries: dict[str, asyncio.TimerHandle] = {}

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def get_flow_locked(self, flow_id: str) -> OAuthState | None:
        return self._flows.get(flow_id)

    def remember_flow_locked(self, flow: OAuthState) -> None:
        if flow.flow_id is None:
            raise ValueError("flow_id is required")
        self.prune_expired_pending_browser_flows_locked()
        self.compat_success = False
        self._flows[flow.flow_id] = OAuthState(
            flow_id=flow.flow_id,
            method=flow.method,
            expires_at=flow.expires_at,
            started_at=flow.started_at,
            poll_task=flow.poll_task,
            stop_poll=flow.stop_poll,
        )

    def pending_flow_ages_locked(self) -> list[float]:

        self.prune_expired_pending_browser_flows_locked()
        now = time.time()
        ages = sorted(
            round(now - (flow.started_at or now), 1)
            for flow in self._flows.values()
            if flow.status == "pending" and (flow.expires_at is None or flow.expires_at > now)
        )
        if not ages and (self._callback_server is not None or self._callback_server_stop_task is not None):
            ages = [round(now - (self._callback_server_bound_at or now), 1)]
        return ages

    def has_pending_browser_flows_locked(self) -> bool:
        self.prune_expired_pending_browser_flows_locked()
        return any(flow.method == "browser" for flow in self._flows.values())

    async def reset(self) -> None:
        async with self._lock:
            tasks = {flow.poll_task for flow in self._flows.values() if flow.poll_task is not None}
            for flow in self._flows.values():
                if flow.stop_poll is not None:
                    flow.stop_poll.set()
            binds = set(self._callback_server_bind_tasks)
            for timer in self.callback_expiries.values():
                timer.cancel()
            self.callback_expiries.clear()
            self._flows.clear()
            self.compat_success = False
        await asyncio.gather(*tasks, *binds, return_exceptions=True)
        async with self._lock:
            server = self._callback_server
            stop = self._callback_server_stop_task
            self._callback_server = None
            self._callback_server_bound_at = None
            self._callback_server_stop_task = None
        if stop is not None:
            await stop
        elif server is not None:
            await server.stop()

    def prune_expired_pending_browser_flows_locked(self) -> None:
        now = time.time()
        expired_flows = [
            flow
            for flow in self._flows.values()
            if flow.method == "browser"
            and flow.status == "pending"
            and flow.expires_at is not None
            and flow.expires_at <= now
        ]
        for flow in expired_flows:
            self.remove_flow_locked(flow)

    def remove_flow_locked(self, flow: OAuthState) -> None:
        if flow.flow_id is not None:
            self._flows.pop(flow.flow_id, None)


class OAuthCallbackServer:
    def __init__(
        self,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
        host: str = "127.0.0.1",
        port: int = 1455,
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
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
