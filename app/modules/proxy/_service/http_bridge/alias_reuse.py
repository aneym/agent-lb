from __future__ import annotations

from app.modules.proxy._service.http_bridge.acquisition_state import BridgeSessionAcquisition
from app.modules.proxy._service.http_bridge.helpers import (
    _durable_bridge_lookup_allows_local_reuse,
    _http_bridge_previous_response_alias_key,
    _http_bridge_session_allows_api_key,
    _http_bridge_session_matches_preferred_account,
    _http_bridge_session_retiring_with_visible_requests,
    _http_bridge_session_reusable_for_request,
    _http_bridge_turn_state_alias_key,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.operation_contract import BridgeOperationsProtocol
from app.modules.proxy._service.http_bridge.service_stubs import _service_time
from app.modules.proxy._service.support import _HTTPBridgeSession, _HTTPBridgeSessionKey
from app.modules.proxy.affinity import _extract_model_class


class BridgeAliasReuseMixin(BridgeOperationsProtocol):
    async def resolve_bridge_turn_alias(self, state: BridgeSessionAcquisition) -> bool:
        if (
            state.incoming_turn_state is not None
            and state.forwarded_affinity is None
            and (not state.preserve_durable_canonical_key)
        ):
            state.alias_index_key = _http_bridge_turn_state_alias_key(state.incoming_turn_state, state.api_key_id)
            state.alias_key = self._http_bridge_turn_state_index.get(state.alias_index_key)
            if state.alias_key is not None:
                state.key = state.alias_key
                state.alias_session = self._http_bridge_sessions.get(state.alias_key)
                if (
                    state.alias_session is None
                    or state.alias_session.closed
                    or (not await self._http_bridge_account_still_servable(state.alias_session.account))
                    or (
                        not _http_bridge_session_matches_preferred_account(
                            session=state.alias_session,
                            previous_response_id=state.previous_response_id,
                            preferred_account_id=state.preferred_account_id,
                            require_preferred_account=not state.fallback_on_preferred_account_unavailable,
                        )
                    )
                ):
                    self._http_bridge_turn_state_index.pop(state.alias_index_key, None)
                    state.key = _HTTPBridgeSessionKey("turn_state_header", state.incoming_turn_state, state.api_key_id)
                else:
                    self._promote_http_bridge_session_to_codex_affinity(
                        state.alias_session, turn_state=state.incoming_turn_state, settings=state.settings
                    )
                    for alias in state.alias_session.downstream_turn_state_aliases:
                        self._http_bridge_turn_state_index[
                            _http_bridge_turn_state_alias_key(alias, state.alias_session.key.api_key_id)
                        ] = state.alias_session.key
                    state.key = state.alias_session.key
            elif state.incoming_turn_state.startswith("http_turn_"):
                if state.previous_response_id is not None:
                    state.previous_alias_key = _http_bridge_previous_response_alias_key(
                        state.previous_response_id, state.api_key_id
                    )
                    state.previous_key = self._http_bridge_previous_response_index.get(state.previous_alias_key)
                    state.previous_session = None
                    if state.previous_key is not None:
                        state.previous_session = self._http_bridge_sessions.get(state.previous_key)
                    if (
                        state.previous_session is not None
                        and (not state.previous_session.closed)
                        and await self._http_bridge_account_still_servable(state.previous_session.account)
                        and _http_bridge_session_matches_preferred_account(
                            session=state.previous_session,
                            previous_response_id=state.previous_response_id,
                            preferred_account_id=state.preferred_account_id,
                            require_preferred_account=not state.fallback_on_preferred_account_unavailable,
                        )
                    ):
                        state.key = state.previous_session.key
                        self._promote_http_bridge_session_to_codex_affinity(
                            state.previous_session, turn_state=state.incoming_turn_state, settings=state.settings
                        )
                        state.previous_session.downstream_turn_state_aliases.add(state.incoming_turn_state)
                        for alias in state.previous_session.downstream_turn_state_aliases:
                            self._http_bridge_turn_state_index[
                                _http_bridge_turn_state_alias_key(alias, state.previous_session.key.api_key_id)
                            ] = state.previous_session.key
                        return True
                    if state.previous_key is not None:
                        self._http_bridge_previous_response_index.pop(state.previous_alias_key, None)
                if state.incoming_session_key is not None:
                    state.key = _HTTPBridgeSessionKey("session_header", state.incoming_session_key, state.api_key_id)
                    state.used_session_header_fallback = True
                else:
                    state.key = _HTTPBridgeSessionKey("turn_state_header", state.incoming_turn_state, state.api_key_id)
                    state.missing_turn_state_alias = True
        return False

    async def reuse_bridge_session(self, state: BridgeSessionAcquisition) -> _HTTPBridgeSession | None:
        state.pruned_sessions = self._prune_http_bridge_sessions_locked()
        if state.pruned_sessions:
            if any((session.key == state.key for session in state.pruned_sessions)):
                state.force_durable_takeover = True
            self._schedule_http_bridge_session_closes(state.pruned_sessions, reason="registry_detach")
        state.existing = self._http_bridge_sessions.get(state.key)
        if (
            state.existing is not None
            and (not state.existing.closed)
            and await self._http_bridge_account_still_servable(state.existing.account)
            and _http_bridge_session_allows_api_key(state.existing, state.api_key)
            and _http_bridge_session_reusable_for_request(
                session=state.existing,
                key=state.key,
                incoming_turn_state=state.incoming_turn_state,
                previous_response_id=state.previous_response_id,
            )
            and _http_bridge_session_matches_preferred_account(
                session=state.existing,
                previous_response_id=state.previous_response_id,
                preferred_account_id=state.preferred_account_id,
                require_preferred_account=not state.fallback_on_preferred_account_unavailable,
            )
        ):
            state.current_instance = state.settings.http_responses_session_bridge_instance_id
            if _durable_bridge_lookup_allows_local_reuse(state.durable_lookup, current_instance=state.current_instance):
                state.existing.api_key = state.api_key
                state.existing.request_model = state.request_model
                state.existing.last_used_at = _service_time().monotonic()
                await self._refresh_durable_http_bridge_session(state.existing)
                _log_http_bridge_event(
                    "reuse",
                    state.key,
                    account_id=state.existing.account.id,
                    model=state.existing.request_model,
                    pending_count=self._http_bridge_pending_count_nowait(state.existing, context="reuse_log"),
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.existing.request_model)
                    if state.existing.request_model
                    else None,
                )
                return state.existing
            state.old_account_id = state.existing.account.id
            state.detached = self._detach_http_bridge_session_locked(state.key, expected_session=state.existing)
            if state.detached is not None:
                state.force_durable_takeover = True
                self._schedule_http_bridge_session_closes([state.detached], reason="registry_detach")
            state.existing = None
        if (
            state.existing is not None
            and (not state.existing.closed)
            and await self._http_bridge_account_still_servable(state.existing.account)
        ):
            state.old_account_id = state.existing.account.id
            state.retiring_with_visible_requests = _http_bridge_session_retiring_with_visible_requests(state.existing)
            state.detached = self._detach_http_bridge_session_locked(
                state.key, expected_session=state.existing, mark_closed=not state.retiring_with_visible_requests
            )
            if state.detached is not None:
                state.force_durable_takeover = True
                if not state.retiring_with_visible_requests:
                    self._schedule_http_bridge_session_closes([state.detached], reason="registry_detach")
            state.existing = None
        return None
