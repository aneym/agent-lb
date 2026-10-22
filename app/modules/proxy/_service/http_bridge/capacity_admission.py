from __future__ import annotations

import asyncio

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.resilience.overload import local_overload_error
from app.modules.proxy._service.http_bridge.acquisition_state import BridgeSessionAcquisition
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_continuity_lost_error_envelope,
    _http_bridge_eviction_priority,
    _http_bridge_previous_response_alias_key,
    _http_bridge_turn_state_alias_key,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.operation_contract import BridgeOperationsProtocol
from app.modules.proxy._service.http_bridge.service_stubs import _record_continuity_fail_closed, _service_time
from app.modules.proxy.affinity import _extract_model_class


class BridgeCapacityAdmissionMixin(BridgeOperationsProtocol):
    async def admit_bridge_session(self, state: BridgeSessionAcquisition) -> None:
        if state.owner_mismatch_error is None:
            state.inflight_future = self._http_bridge_inflight_sessions.get(state.key)
            if (
                state.previous_response_id is not None
                and state.inflight_future is None
                and (
                    state.existing is None
                    or state.existing.closed
                    or (not await self._http_bridge_account_still_servable(state.existing.account))
                )
            ):
                state.previous_alias_key = _http_bridge_previous_response_alias_key(
                    state.previous_response_id, state.api_key_id
                )
                state.previous_key = self._http_bridge_previous_response_index.get(state.previous_alias_key)
                if state.previous_key is not None:
                    state.previous_session = self._http_bridge_sessions.get(state.previous_key)
                    if (
                        state.previous_session is not None
                        and (not state.previous_session.closed)
                        and await self._http_bridge_account_still_servable(state.previous_session.account)
                    ):
                        state.key = state.previous_session.key
                        state.existing = state.previous_session
                        state.inflight_future = self._http_bridge_inflight_sessions.get(state.previous_key)
                        if state.incoming_turn_state:
                            self._promote_http_bridge_session_to_codex_affinity(
                                state.previous_session, turn_state=state.incoming_turn_state, settings=state.settings
                            )
                            state.previous_session.downstream_turn_state_aliases.add(state.incoming_turn_state)
                            for alias in state.previous_session.downstream_turn_state_aliases:
                                self._http_bridge_turn_state_index[
                                    _http_bridge_turn_state_alias_key(alias, state.previous_session.key.api_key_id)
                                ] = state.previous_session.key
                        if state.inflight_future is None:
                            state.previous_session.request_model = state.request_model
                            state.previous_session.last_used_at = _service_time().monotonic()
                            await self._refresh_durable_http_bridge_session(state.previous_session)
                            _log_http_bridge_event(
                                "reuse",
                                state.key,
                                account_id=state.previous_session.account.id,
                                model=state.previous_session.request_model,
                                pending_count=self._http_bridge_pending_count_nowait(
                                    state.previous_session, context="previous_response_reuse_log"
                                ),
                                cache_key_family=state.key.affinity_kind,
                                model_class=_extract_model_class(state.previous_session.request_model)
                                if state.previous_session.request_model
                                else None,
                            )
                            state.session_to_return_after_close = state.previous_session
                    else:
                        self._http_bridge_previous_response_index.pop(state.previous_alias_key, None)
            if (
                state.session_to_return_after_close is None
                and state.previous_response_id is not None
                and (not state.used_session_header_fallback)
                and (not state.allow_previous_response_recovery_rebind)
                and (state.durable_lookup is None)
            ):
                _record_continuity_fail_closed(
                    surface="http_bridge",
                    reason="continuity_lost",
                    previous_response_id=state.previous_response_id,
                    session_id=state.incoming_turn_state or state.incoming_session_key,
                )
                state.continuity_error = ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())
            elif state.missing_turn_state_alias and state.inflight_future is None and (state.durable_lookup is None):
                state.turn_state_scope_conflict = state.incoming_turn_state is not None and any(
                    (
                        alias == state.incoming_turn_state and alias_api_key != state.api_key_id
                        for alias, alias_api_key in self._http_bridge_turn_state_index
                    )
                )
                if state.turn_state_scope_conflict:
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="turn_state_scope_conflict",
                        previous_response_id=state.previous_response_id,
                        session_id=state.incoming_turn_state,
                    )
                    state.continuity_error = ProxyResponseError(
                        409,
                        openai_error(
                            "bridge_instance_mismatch",
                            "HTTP bridge turn-state is bound to a different API key scope",
                            error_type="server_error",
                        ),
                    )
                elif (
                    state.incoming_turn_state is not None
                    and state.incoming_turn_state.startswith("http_turn_")
                    and (not state.allow_forward_to_owner)
                ):
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="generated_turn_state_continuity_lost",
                        previous_response_id=state.previous_response_id,
                        session_id=state.incoming_turn_state,
                    )
                    state.continuity_error = ProxyResponseError(
                        409,
                        openai_error(
                            "bridge_instance_mismatch",
                            "HTTP bridge continuity was lost for generated turn-state",
                            error_type="server_error",
                        ),
                    )
                else:
                    _log_http_bridge_event(
                        "turn_state_alias_miss_local_rebind",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail="outcome=local_rebind_without_alias",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=state.owner_check_required,
                    )
            elif state.inflight_future is None:
                while (
                    len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= state.max_sessions
                    and self._http_bridge_sessions
                ):
                    state.evictable_sessions = []
                    for state.candidate_key, state.candidate_session in self._http_bridge_sessions.items():
                        state.pending_count = self._http_bridge_pending_count_nowait(
                            state.candidate_session, context="capacity_evict_scan"
                        )
                        if state.pending_count is None:
                            continue
                        if state.pending_count:
                            continue
                        state.evictable_sessions.append((state.candidate_key, state.candidate_session))
                    if not state.evictable_sessions:
                        break
                    state.lru_key, state.lru_session = min(
                        state.evictable_sessions, key=lambda item: _http_bridge_eviction_priority(item[1])
                    )
                    _log_http_bridge_event(
                        "evict_lru",
                        state.lru_key,
                        account_id=state.lru_session.account.id,
                        model=state.lru_session.request_model,
                        cache_key_family=state.lru_key.affinity_kind,
                        model_class=_extract_model_class(state.lru_session.request_model)
                        if state.lru_session.request_model
                        else None,
                    )
                    state.detached = self._detach_http_bridge_session_locked(
                        state.lru_key, expected_session=state.lru_session
                    )
                    if state.detached is not None:
                        state.sessions_to_close_before_create.append(state.detached)
                if len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= state.max_sessions:
                    if self._http_bridge_inflight_sessions:
                        state.capacity_wait_future = next(iter(self._http_bridge_inflight_sessions.values()))
                    else:
                        _log_http_bridge_event(
                            "capacity_exhausted_active_sessions",
                            state.key,
                            account_id=None,
                            model=state.request_model,
                            pending_count=len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions),
                            cache_key_family=state.key.affinity_kind,
                            model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        )
                        raise ProxyResponseError(
                            429,
                            local_overload_error(
                                "HTTP responses session bridge has no idle capacity",
                                code="capacity_exhausted_active_sessions",
                            ),
                        )
                else:
                    state.inflight_future = asyncio.get_running_loop().create_future()
                    self._http_bridge_inflight_sessions[state.key] = state.inflight_future
                    state.owns_creation = True
