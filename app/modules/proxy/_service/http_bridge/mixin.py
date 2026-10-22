from __future__ import annotations

import asyncio
from typing import Literal, overload

from app.core import shutdown as shutdown_state
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyRequestUsageBudget
from app.modules.proxy._service.support import _HTTPBridgeOwnerForward, _HTTPBridgeSession, _HTTPBridgeSessionKey
from app.modules.proxy.affinity import (
    _AffinityPolicy,
    _extract_model_class,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup

from .acquisition_state import BridgeSessionAcquisition
from .alias_reuse import BridgeAliasReuseMixin
from .capacity_admission import BridgeCapacityAdmissionMixin
from .durable_session import BridgeDurableSessionMixin
from .helpers import (
    _durable_bridge_lookup_allows_local_reuse,
    _forwarded_http_bridge_session_key,
    _http_bridge_can_recover_during_drain,
    _http_bridge_owner_check_required,
    _http_bridge_session_allows_api_key,
    _http_bridge_session_matches_preferred_account,
    _http_bridge_session_retiring_with_visible_requests,
    _http_bridge_session_reusable_for_request,
    _http_bridge_should_wait_for_registration,
    _http_bridge_startup_wait_timeout_error,
    _http_bridge_turn_state_alias_key,
    _log_http_bridge_event,
    _log_http_bridge_startup_wait_timeout,
    _record_bridge_drain_recovery_allowed,
)
from .lifecycle import BridgeLifecycleMixin
from .owner_forwarding import _HTTPBridgeOwnerForwardingMixin
from .owner_recovery import BridgeOwnerRecoveryMixin
from .protocol import _HTTPBridgeServiceProtocol
from .request_submit import _HTTPBridgeRequestSubmitMixin
from .service_stubs import _proxy_admission_wait_timeout_seconds, _service_get_settings, _service_time
from .session_connect import BridgeSessionConnectMixin
from .session_reconnect import BridgeSessionReconnectMixin
from .session_registration import BridgeSessionRegistrationMixin
from .session_registry import _HTTPBridgeSessionRegistryMixin
from .streaming import _HTTPBridgeStreamingMixin
from .upstream_events import _HTTPBridgeUpstreamEventsMixin


class _HTTPBridgeMixin(
    BridgeLifecycleMixin,
    BridgeDurableSessionMixin,
    BridgeSessionConnectMixin,
    BridgeSessionReconnectMixin,
    BridgeOwnerRecoveryMixin,
    BridgeAliasReuseMixin,
    BridgeCapacityAdmissionMixin,
    BridgeSessionRegistrationMixin,
    _HTTPBridgeSessionRegistryMixin,
    _HTTPBridgeStreamingMixin,
    _HTTPBridgeOwnerForwardingMixin,
    _HTTPBridgeRequestSubmitMixin,
    _HTTPBridgeUpstreamEventsMixin,
    _HTTPBridgeServiceProtocol,
):
    @overload
    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        response_create_concurrency: int = 64,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: Literal[False] = False,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession": ...

    @overload
    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        response_create_concurrency: int = 64,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: Literal[True],
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward": ...

    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        response_create_concurrency: int = 64,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: bool = False,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward":
        state = BridgeSessionAcquisition(
            key=key,
            headers=headers,
            affinity=affinity,
            api_key=api_key,
            request_model=request_model,
            idle_ttl_seconds=idle_ttl_seconds,
            max_sessions=max_sessions,
            response_create_concurrency=response_create_concurrency,
            previous_response_id=previous_response_id,
            gateway_safe_mode=gateway_safe_mode,
            allow_forward_to_owner=allow_forward_to_owner,
            forwarded_request=forwarded_request,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            allow_previous_response_recovery_rebind=allow_previous_response_recovery_rebind,
            allow_bootstrap_owner_rebind=allow_bootstrap_owner_rebind,
            durable_lookup=durable_lookup,
            request_stage=request_stage,
            preferred_account_id=preferred_account_id,
            fallback_on_preferred_account_unavailable=fallback_on_preferred_account_unavailable,
            request_usage_budget=request_usage_budget,
        )
        state.settings = _service_get_settings()
        state.api_key_id = state.api_key.id if state.api_key is not None else None
        state.incoming_turn_state = _sticky_key_from_turn_state_header(state.headers)
        state.incoming_session_key = _sticky_key_from_session_header(state.headers)
        if await _http_bridge_should_wait_for_registration(self, state.key, state.settings):
            state.skip_registration_gate = False
            async with self._http_bridge_lock:
                state.existing = self._http_bridge_sessions.get(state.key)
                if state.existing is not None:
                    state.skip_registration_gate = True
                elif state.incoming_turn_state is not None:
                    state.alias_index_key = _http_bridge_turn_state_alias_key(
                        state.incoming_turn_state, state.api_key_id
                    )
                    state.alias_key = self._http_bridge_turn_state_index.get(state.alias_index_key)
                    if state.alias_key is not None and state.alias_key in self._http_bridge_sessions:
                        state.skip_registration_gate = True
            if not state.skip_registration_gate:
                import app.core.startup as startup_module

                state.registered = await startup_module.wait_for_bridge_registration(
                    timeout_seconds=state.settings.upstream_connect_timeout_seconds
                )
                if not state.registered:
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_owner_unreachable",
                            "HTTP bridge registration is not ready",
                            error_type="server_error",
                        ),
                    )
        state.effective_idle_ttl_seconds = state.idle_ttl_seconds
        state.forwarded_affinity = (
            _forwarded_http_bridge_session_key(
                state.headers,
                state.api_key,
                forwarded_affinity_kind=state.forwarded_affinity_kind,
                forwarded_affinity_key=state.forwarded_affinity_key,
            )
            if state.forwarded_request
            else None
        )
        state.old_account_id = None
        state.force_durable_takeover_after_detach = False
        while True:
            state.inflight_future = None
            state.capacity_wait_future = None
            state.owns_creation = False
            state.continuity_error = None
            state.owner_mismatch_error = None
            state.owner_forward = None
            state.force_durable_takeover = state.force_durable_takeover_after_detach
            state.missing_turn_state_alias = False
            state.used_session_header_fallback = False
            state.sessions_to_close_before_create = []
            state.session_to_return_after_close = None
            state.preserve_durable_canonical_key = (
                state.incoming_turn_state is not None
                and state.forwarded_affinity is None
                and (state.durable_lookup is not None)
                and (state.key.affinity_kind == state.durable_lookup.canonical_kind)
                and (state.key.affinity_key == state.durable_lookup.canonical_key)
                and (state.key.affinity_kind != "turn_state_header")
            )
            async with self._http_bridge_lock:
                if await self.resolve_bridge_turn_alias(state):
                    continue
                reused_session = await self.reuse_bridge_session(state)
                if reused_session is not None:
                    return reused_session
                if shutdown_state.is_bridge_drain_active() and (
                    not _http_bridge_can_recover_during_drain(
                        key=state.key,
                        headers=state.headers,
                        previous_response_id=state.previous_response_id,
                        durable_lookup=state.durable_lookup,
                    )
                ):
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_drain_active",
                            "HTTP bridge is draining — new sessions not accepted during shutdown",
                            error_type="server_error",
                        ),
                    )
                if shutdown_state.is_bridge_drain_active():
                    _record_bridge_drain_recovery_allowed()
                state.owner_check_required = _http_bridge_owner_check_required(
                    state.key, gateway_safe_mode=state.gateway_safe_mode
                )
                await self.resolve_bridge_owner(state)
                if state.existing is not None:
                    state.old_account_id = state.existing.account.id
                    _log_http_bridge_event(
                        "discard_stale",
                        state.key,
                        account_id=state.existing.account.id,
                        model=state.existing.request_model,
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.existing.request_model)
                        if state.existing.request_model
                        else None,
                    )
                    state.detached = self._detach_http_bridge_session_locked(state.key, expected_session=state.existing)
                    if state.detached is not None:
                        state.force_durable_takeover = True
                        self._schedule_http_bridge_session_closes([state.detached], reason="registry_detach")
                await self.admit_bridge_session(state)
            try:
                for state.session_to_close in state.sessions_to_close_before_create:
                    await self._close_http_bridge_session_bounded(state.session_to_close, reason="registry_detach")
            except BaseException as exc:
                if state.owns_creation:
                    await self._fail_http_bridge_inflight_session_creation(state.key, state.inflight_future, exc)
                raise
            if state.session_to_return_after_close is not None:
                return state.session_to_return_after_close
            if state.owner_forward is not None:
                return state.owner_forward
            if state.owner_mismatch_error is not None:
                raise state.owner_mismatch_error
            if state.continuity_error is not None:
                raise state.continuity_error
            if state.capacity_wait_future is not None:
                state.wait_timeout_seconds = _proxy_admission_wait_timeout_seconds(state.settings)
                try:
                    await asyncio.wait_for(
                        asyncio.shield(state.capacity_wait_future), timeout=state.wait_timeout_seconds
                    )
                except asyncio.CancelledError:
                    if state.capacity_wait_future.cancelled():
                        continue
                    raise
                except TimeoutError as exc:
                    state.timeout_error = _http_bridge_startup_wait_timeout_error(
                        "http_bridge_capacity", code="capacity_exhausted_active_sessions"
                    )
                    state.stale_key = await self._evict_http_bridge_inflight_waiter(
                        state.capacity_wait_future, state.timeout_error
                    )
                    _log_http_bridge_startup_wait_timeout(
                        stage="capacity",
                        timeout_seconds=state.wait_timeout_seconds,
                        key=state.stale_key or state.key,
                        request_model=state.request_model,
                        pending_count=len(self._http_bridge_sessions),
                        inflight_count=len(self._http_bridge_inflight_sessions),
                    )
                    raise state.timeout_error from exc
                except ProxyResponseError:
                    raise
                except Exception:
                    pass
                continue
            if state.inflight_future is not None and (not state.owns_creation):
                state.wait_timeout_seconds = _proxy_admission_wait_timeout_seconds(state.settings)
                try:
                    session = await asyncio.wait_for(
                        asyncio.shield(state.inflight_future), timeout=state.wait_timeout_seconds
                    )
                except asyncio.CancelledError:
                    if state.inflight_future.cancelled():
                        continue
                    raise
                except TimeoutError as exc:
                    state.timeout_error = _http_bridge_startup_wait_timeout_error(
                        "http_bridge_inflight_session", code="capacity_exhausted_active_sessions"
                    )
                    await self._fail_http_bridge_inflight_session_creation(
                        state.key, state.inflight_future, state.timeout_error
                    )
                    _log_http_bridge_startup_wait_timeout(
                        stage="inflight_session",
                        timeout_seconds=state.wait_timeout_seconds,
                        key=state.key,
                        request_model=state.request_model,
                        pending_count=len(self._http_bridge_sessions),
                        inflight_count=len(self._http_bridge_inflight_sessions),
                    )
                    raise state.timeout_error from exc
                except Exception:
                    raise
                if session is None:
                    continue
                if (
                    not session.closed
                    and await self._http_bridge_account_still_servable(session.account)
                    and _http_bridge_session_allows_api_key(session, state.api_key)
                    and _http_bridge_session_reusable_for_request(
                        session=session,
                        key=state.key,
                        incoming_turn_state=state.incoming_turn_state,
                        previous_response_id=state.previous_response_id,
                    )
                    and _http_bridge_session_matches_preferred_account(
                        session=session,
                        previous_response_id=state.previous_response_id,
                        preferred_account_id=state.preferred_account_id,
                        require_preferred_account=not state.fallback_on_preferred_account_unavailable,
                    )
                ):
                    state.current_instance = state.settings.http_responses_session_bridge_instance_id
                    if _durable_bridge_lookup_allows_local_reuse(
                        state.durable_lookup, current_instance=state.current_instance
                    ):
                        session.api_key = state.api_key
                        session.request_model = state.request_model
                        session.last_used_at = _service_time().monotonic()
                        return session
                if not session.closed and await self._http_bridge_account_still_servable(session.account):
                    state.old_account_id = session.account.id
                    state.retiring_with_visible_requests = _http_bridge_session_retiring_with_visible_requests(session)
                    async with self._http_bridge_lock:
                        state.detached = self._detach_http_bridge_session_locked(
                            state.key, expected_session=session, mark_closed=not state.retiring_with_visible_requests
                        )
                    if state.detached is not None:
                        state.force_durable_takeover_after_detach = True
                    if state.detached is not None and (not state.retiring_with_visible_requests):
                        self._schedule_http_bridge_session_closes([state.detached], reason="registry_detach")
                continue
            return await self.create_and_register_bridge_session(state)
