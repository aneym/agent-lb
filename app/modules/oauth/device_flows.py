from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass

from app.core.clients.oauth import OAuthError, request_device_code
from app.modules.oauth.repository import OAuthFlowRecord, epoch_to_naive_utc
from app.modules.oauth.route import _oauth_route
from app.modules.oauth.schemas import OauthStartResponse
from app.modules.oauth.state import OAuthState


class OAuthDeviceFlows:
    async def _start_device_flow(self, *, intended_account_id: str | None = None) -> OauthStartResponse:
        flow_id = secrets.token_urlsafe(12)
        try:
            route = await _oauth_route()
            device = await request_device_code(route=route, allow_direct_egress=route is None)
        except OAuthError as exc:
            await self._set_error(exc.message)
            raise
        expires_at = time.time() + device.expires_in_seconds
        flow = OAuthState(
            flow_id=flow_id,
            status="pending",
            method="device",
            device_auth_id=device.device_auth_id,
            user_code=device.user_code,
            interval_seconds=device.interval_seconds,
            intended_account_id=intended_account_id,
            expires_at=expires_at,
        )
        async with self._store.lock:
            flow = OAuthState(
                flow_id=flow_id,
                status="pending",
                method="device",
                device_auth_id=device.device_auth_id,
                user_code=device.user_code,
                interval_seconds=device.interval_seconds,
                intended_account_id=intended_account_id,
                started_at=time.time(),
                expires_at=expires_at,
            )
            self._store.remove_pending_device_flows_locked()
            self._store.remember_flow_locked(flow)
        await self._persist_flow_record(
            OAuthFlowRecord(
                flow_id=flow_id,
                method="device",
                status="pending",
                device_auth_id=device.device_auth_id,
                user_code=device.user_code,
                interval_seconds=device.interval_seconds,
                intended_account_id=intended_account_id,
                expires_at=epoch_to_naive_utc(expires_at),
            )
        )
        async with self._store.lock:
            if self._store.get_flow_locked(flow_id) is flow:
                await self._claim_device_slot(flow_id)
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

    def _ensure_device_poll_task_locked(self, state: OAuthState) -> bool:
        if state.poll_task and (not state.poll_task.done()):
            return True
        if not state.device_auth_id or not state.user_code or (not state.expires_at):
            return False
        interval = state.interval_seconds if state.interval_seconds is not None else 0
        poll_context = DevicePollContext(
            device_auth_id=state.device_auth_id,
            user_code=state.user_code,
            interval_seconds=max(interval, 0),
            expires_at=state.expires_at,
            intended_account_id=state.intended_account_id,
        )
        state.poll_task = asyncio.create_task(self._poll_device_tokens(state.flow_id, poll_context))
        return True


@dataclass(frozen=True)
class DevicePollContext:
    device_auth_id: str
    user_code: str
    interval_seconds: int
    expires_at: float
    intended_account_id: str | None = None
