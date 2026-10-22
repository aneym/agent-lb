from __future__ import annotations

import asyncio
from collections import deque
from uuid import uuid4

import aiohttp
import anyio

from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.db.models import StickySessionKind
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyRequestUsageBudget
from app.modules.proxy._service.http_bridge.acquisition_state import REQUEST_TRANSPORT_HTTP
from app.modules.proxy._service.http_bridge.helpers import _record_bridge_first_turn_timeout
from app.modules.proxy._service.http_bridge.operation_contract import BridgeOperationsProtocol
from app.modules.proxy._service.http_bridge.service_stubs import (
    _call_with_supported_optional_kwargs,
    _estimated_lease_tokens_from_request_usage_budget,
    _headers_with_turn_state,
    _is_local_account_cap_code,
    _prefer_earlier_reset_window,
    _raise_proxy_unavailable,
    _record_same_account_takeover,
    _remaining_budget_seconds,
    _routing_strategy,
    _service_get_settings,
    _service_get_settings_cache,
    _service_time,
    _upstream_turn_state_from_socket,
    _websocket_connect_deadline,
)
from app.modules.proxy._service.support import (
    _copy_websocket_route_metadata_to_session,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
    _WebSocketRequestState,
    _WebSocketUpstreamControl,
)
from app.modules.proxy.affinity import _AffinityPolicy, _sticky_key_from_turn_state_header
from app.modules.proxy.load_balancer import AccountLease
from app.modules.proxy.selection_diagnostics import enrich_selection_error


class BridgeSessionConnectMixin(BridgeOperationsProtocol):
    async def _create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        response_create_concurrency: int = 64,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        require_preferred_account: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession":
        request_state = _WebSocketRequestState(
            request_id=f"http_bridge_connect_{uuid4().hex}",
            model=request_model,
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=_service_time().monotonic(),
            transport=REQUEST_TRANSPORT_HTTP,
        )
        deadline = _websocket_connect_deadline(request_state, _service_get_settings().proxy_request_budget_seconds)
        settings = await _service_get_settings_cache().get()
        excluded_account_ids: set[str] = set()
        cooldown = self._http_bridge_account_cooldowns.get(key)
        if cooldown is not None:
            if _service_time().monotonic() >= cooldown.expires_at:
                self._http_bridge_account_cooldowns.pop(key, None)
            elif cooldown.account_id != preferred_account_id:
                excluded_account_ids.add(cooldown.account_id)
        retry_same_account_once = preferred_account_id is not None
        preferred_candidate_id = preferred_account_id
        selected_account_lease: AccountLease | None = None

        account_rejection: ProxyResponseError | None = None
        while True:
            select_kwargs = {
                "request_id": request_state.request_log_id or request_state.request_id,
                "kind": "http_bridge",
                "request_stage": request_stage,
                "api_key": api_key,
                "sticky_key": affinity.key,
                "sticky_kind": affinity.kind,
                "reallocate_sticky": affinity.reallocate_sticky,
                "sticky_max_age_seconds": affinity.max_age_seconds,
                "prefer_earlier_reset_accounts": settings.prefer_earlier_reset_accounts,
                "prefer_earlier_reset_window": _prefer_earlier_reset_window(settings),
                "routing_strategy": _routing_strategy(settings),
                "model": request_model,
                "exclude_account_ids": excluded_account_ids,
                "preferred_account_id": preferred_candidate_id,
                "lease_kind": "stream",
                "estimated_lease_tokens": _estimated_lease_tokens_from_request_usage_budget(request_usage_budget),
                "fallback_on_preferred_account_unavailable": fallback_on_preferred_account_unavailable,
            }
            selection = await self._select_account_with_budget_for_stream(deadline, **select_kwargs)
            selected_account_lease = selection.lease
            account = selection.account
            if account is not None and account_rejection is not None and account.id in excluded_account_ids:
                account = None
            if account is None:
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=None,
                )
                if account_rejection is not None:
                    raise account_rejection
                status_code = 429 if _is_local_account_cap_code(selection.error_code) else 503
                error_type = "rate_limit_error" if status_code == 429 else "server_error"
                raise ProxyResponseError(
                    status_code,
                    enrich_selection_error(
                        openai_error(
                            selection.error_code or "no_accounts",
                            selection.error_message or "No active accounts available",
                            error_type=error_type,
                        ),
                        selection,
                        requested_model=request_model,
                    ),
                )
            if require_preferred_account and preferred_account_id is not None and account.id != preferred_account_id:
                message = "Previous response owner account is unavailable; retry later."
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "previous_response_owner_unavailable",
                        message,
                        error_type="server_error",
                    ),
                )
            selected_is_preferred = preferred_account_id is not None and account.id == preferred_account_id
            try:
                account = await self._ensure_fresh_with_budget(
                    account,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                connect_headers = _headers_with_turn_state(headers, _sticky_key_from_turn_state_header(headers))
                upstream = await _call_with_supported_optional_kwargs(
                    self._open_upstream_websocket_with_budget,
                    account,
                    connect_headers,
                    optional_kwargs={"request_state": request_state},
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                break
            except ProxyResponseError as exc:
                if (
                    exc.status_code == 403
                    and _remaining_budget_seconds(deadline) > 0
                    and not (require_preferred_account and selected_is_preferred)
                ):
                    await self._handle_proxy_error(account, exc)
                    account_rejection = exc
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                if exc.status_code != 401 or _remaining_budget_seconds(deadline) <= 0:
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    raise
                try:
                    account = await self._ensure_fresh_with_budget(
                        account,
                        force=True,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                    )
                    connect_headers = _headers_with_turn_state(headers, _sticky_key_from_turn_state_header(headers))
                    upstream = await self._open_upstream_websocket_with_budget(
                        account,
                        connect_headers,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                        request_state=request_state,
                    )
                    _record_same_account_takeover(
                        preferred_account_id=preferred_account_id,
                        selected_account_id=account.id,
                    )
                    break
                except ProxyResponseError as retry_exc:
                    if retry_exc.status_code != 401:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    await self._handle_proxy_error(account, retry_exc)
                    if require_preferred_account and selected_is_preferred:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    if require_preferred_account and selected_is_preferred:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once and not exc.is_permanent:
                        retry_same_account_once = False
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        continue
                    if require_preferred_account:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise ProxyResponseError(
                            503,
                            openai_error(
                                "no_accounts",
                                "Preferred account is unavailable; retry later.",
                                error_type="server_error",
                            ),
                        ) from exc
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                if exc.is_permanent:
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    rejection = ProxyResponseError(
                        401,
                        openai_error(
                            "invalid_api_key",
                            exc.message,
                            error_type="authentication_error",
                        ),
                    )
                    if _remaining_budget_seconds(deadline) <= 0:
                        raise rejection from exc
                    account_rejection = rejection
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    continue
                if request_stage == "first_turn":
                    _record_bridge_first_turn_timeout()
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _raise_proxy_unavailable(exc.message or "Temporary upstream refresh failure")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        continue
                    if require_preferred_account:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise ProxyResponseError(
                            503,
                            openai_error(
                                "no_accounts",
                                "Preferred account is unavailable; retry later.",
                                error_type="server_error",
                            ),
                        ) from exc
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                if request_stage == "first_turn":
                    _record_bridge_first_turn_timeout()
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")
            except BaseException:
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                raise
        session = _HTTPBridgeSession(
            key=key,
            headers=connect_headers,
            affinity=affinity,
            api_key=api_key,
            request_model=request_model,
            account=account,
            upstream=upstream,
            upstream_control=_WebSocketUpstreamControl(),
            pending_requests=deque(),
            pending_lock=anyio.Lock(),
            response_create_gate=asyncio.Semaphore(response_create_concurrency),
            queued_request_count=0,
            lifecycle_lock=anyio.Lock(),
            last_used_at=_service_time().monotonic(),
            idle_ttl_seconds=idle_ttl_seconds,
            codex_session=affinity.kind == StickySessionKind.CODEX_SESSION,
            prewarm_lock=anyio.Lock(),
            upstream_turn_state=_upstream_turn_state_from_socket(upstream),
            downstream_turn_state=None,
            account_lease=selected_account_lease,
        )
        _copy_websocket_route_metadata_to_session(session, request_state)
        session.upstream_reader = asyncio.create_task(self._relay_http_bridge_upstream_messages(session))
        return session
