from __future__ import annotations

import asyncio
import logging
import secrets
import time
from abc import ABC, abstractmethod

from app.core.clients.oauth import OAuthError, OAuthTokens, exchange_device_token, request_device_code
from app.core.config.settings import get_settings
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.modules.accounts.identity_repository import AccountIdentityConflictError
from app.modules.oauth.account_bind import ACCOUNT_IDENTITY_CONFLICT_MESSAGE
from app.modules.oauth.durable import DurableOAuthFlows
from app.modules.oauth.schemas import OauthStartRequest, OauthStartResponse
from app.modules.oauth.state import OAUTH_FLOW_TTL_SECONDS, ExpectedAccount, OAuthState, OAuthStateStore

logger = logging.getLogger(__name__)


class DeviceOAuthHandlers(ABC):
    _flows: DurableOAuthFlows
    _store: OAuthStateStore

    @staticmethod
    @abstractmethod
    async def _oauth_route() -> ResolvedUpstreamRoute | None: ...

    @abstractmethod
    async def _persist_tokens(self, tokens: OAuthTokens, flow: OAuthState) -> int | None: ...

    async def _start_device_flow(
        self,
        request: OauthStartRequest,
        expected: ExpectedAccount | None,
    ) -> OauthStartResponse:
        route = await self._oauth_route()
        device = await request_device_code(route=route, allow_direct_egress=route is None)
        started = time.time()
        expires_in = min(device.expires_in_seconds, OAUTH_FLOW_TTL_SECONDS)
        flow = OAuthState(
            flow_id=secrets.token_urlsafe(12),
            method="device",
            device_auth_id=device.device_auth_id,
            user_code=device.user_code,
            interval_seconds=device.interval_seconds,
            started_at=started,
            expires_at=started + expires_in,
            expected_account=expected,
            warmup_hours=request.warmup_hours,
            warmup_capacity_percent=request.warmup_capacity_percent,
            warmup_max_concurrent=request.warmup_max_concurrent,
        )
        await self._flows.insert(flow)
        await self._ensure_device_poll_task(flow)
        return OauthStartResponse(
            flow_id=flow.flow_id,
            method="device",
            verification_url=device.verification_url,
            user_code=device.user_code,
            device_auth_id=device.device_auth_id,
            interval_seconds=device.interval_seconds,
            expires_in_seconds=expires_in,
            expected_account_id=request.expected_account_id,
        )

    async def _ensure_device_poll_task(self, flow: OAuthState) -> None:
        assert flow.flow_id is not None
        async with self._store.lock:
            tracked = self._store.get_flow_locked(flow.flow_id)
            if tracked is not None and tracked.poll_task is not None and not tracked.poll_task.done():
                return
            flow.stop_poll = asyncio.Event()
            flow.poll_task = asyncio.create_task(self._poll_device_tokens(flow.flow_id, flow.stop_poll))
            self._store.remember_flow_locked(flow)

    async def _poll_device_tokens(self, flow_id: str, stop: asyncio.Event) -> None:
        owner = secrets.token_urlsafe(16)
        poll_timeout = 2 * get_settings().oauth_timeout_seconds + 5
        try:
            while not stop.is_set():
                now = time.time()
                flow = await self._flows.claimed_device_poll(flow_id, owner, until=now + poll_timeout + 5)
                if flow is None:
                    flow = await self._flows.get(flow_id)
                    if flow is None or flow.status != "pending" or flow.expires_at is None:
                        return
                    retry_at = min(flow.poll_after or now, flow.expires_at)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=max(0.1, retry_at - time.time()))
                    except TimeoutError:
                        pass
                    continue
                if await self._poll_claimed_device(flow_id, owner, flow, poll_timeout):
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("OAuth device polling failed exception_type=%s", type(exc).__name__)
        finally:
            try:
                await self._flows.release_device_poll(flow_id, owner, poll_after=None)
            finally:
                async with self._store.lock:
                    tracked = self._store.get_flow_locked(flow_id)
                    if tracked is not None and tracked.poll_task is asyncio.current_task():
                        self._store.remove_flow_locked(tracked)

    async def _poll_claimed_device(
        self,
        flow_id: str,
        owner: str,
        flow: OAuthState,
        poll_timeout: float,
    ) -> bool:
        assert flow.device_auth_id and flow.user_code
        claimed = False
        try:
            async with asyncio.timeout(poll_timeout):
                route = await self._oauth_route()
                tokens = await exchange_device_token(
                    device_auth_id=flow.device_auth_id,
                    user_code=flow.user_code,
                    route=route,
                    allow_direct_egress=route is None,
                )
            if tokens is None:
                poll_after = time.time() + max(flow.interval_seconds or 0, 1)
                await self._flows.release_device_poll(flow_id, owner, poll_after=poll_after)
                return False
            claimed = await self._flows.claim(flow_id, poll_owner=owner)
            if claimed:
                probe_status_code = await self._persist_tokens(tokens, flow)
                await self._flows.finish(
                    flow_id,
                    from_status="verifying" if probe_status_code is not None else "processing",
                    error=None,
                    probe_status_code=probe_status_code,
                )
        except asyncio.CancelledError:
            if claimed:
                await self._flows.finish(flow_id, error="Sign-in was interrupted. Check the account before retrying.")
            raise
        except Exception as exc:
            if isinstance(exc, OAuthError):
                message = exc.message
            elif isinstance(exc, AccountIdentityConflictError):
                message = ACCOUNT_IDENTITY_CONFLICT_MESSAGE
            else:
                logger.error("OAuth device exchange failed exception_type=%s", type(exc).__name__)
                message = "An internal error occurred."
            await self._flows.finish(
                flow_id,
                from_status="processing" if claimed else "pending",
                error=message,
                poll_owner=None if claimed else owner,
            )
        return True
