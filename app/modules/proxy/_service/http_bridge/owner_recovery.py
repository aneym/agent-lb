from __future__ import annotations

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    bridge_local_rebind_total,
    bridge_owner_mismatch_total,
    bridge_prompt_cache_locality_miss_total,
    bridge_soft_local_rebind_total,
)
from app.modules.proxy._service.http_bridge.acquisition_state import BridgeSessionAcquisition, logger
from app.modules.proxy._service.http_bridge.helpers import (
    _active_http_bridge_instance_ring,
    _durable_bridge_lookup_active_owner,
    _http_bridge_can_local_recover_without_ring,
    _http_bridge_can_single_instance_owner_takeover_without_anchor,
    _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor,
    _http_bridge_endpoint_matches_current_instance,
    _http_bridge_has_durable_recovery_anchor,
    _http_bridge_key_strength,
    _http_bridge_owner_instance,
    _http_bridge_owner_lookup_unavailable_error_envelope,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.operation_contract import BridgeOperationsProtocol
from app.modules.proxy._service.http_bridge.service_stubs import _record_continuity_fail_closed
from app.modules.proxy._service.support import _HTTPBridgeOwnerForward
from app.modules.proxy.affinity import _extract_model_class


class BridgeOwnerRecoveryMixin(BridgeOperationsProtocol):
    async def resolve_bridge_owner_forward(self, state: BridgeSessionAcquisition) -> None:
        if state.forwarded_request:
            _log_http_bridge_event(
                "owner_mismatch_forward_loop",
                state.key,
                account_id=None,
                model=state.request_model,
                detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=forward_loop_prevented",
                cache_key_family=state.key.affinity_kind,
                model_class=_extract_model_class(state.request_model) if state.request_model else None,
                owner_check_applied=True,
            )
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_forward_loop_prevented",
                    "HTTP bridge request was forwarded back to a non-owner instance; refusing takeover to avoid a forward loop",
                    error_type="server_error",
                ),
            )
        elif self._ring_membership is None:
            if _http_bridge_has_durable_recovery_anchor(
                previous_response_id=state.previous_response_id, durable_lookup=state.durable_lookup
            ):
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="owner_missing").inc()
                _log_http_bridge_event(
                    "owner_mismatch_local_recover",
                    state.key,
                    account_id=None,
                    model=state.request_model,
                    detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_no_ring",
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.request_model) if state.request_model else None,
                    owner_check_applied=True,
                )
                state.force_durable_takeover = True
            elif _http_bridge_can_single_instance_owner_takeover_without_anchor(
                key=state.key,
                owner_instance=state.owner_instance,
                current_instance=state.current_instance,
                ring=state.ring,
            ):
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _log_http_bridge_event(
                    "owner_mismatch_local_recover",
                    state.key,
                    account_id=None,
                    model=state.request_model,
                    detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=single_instance_takeover_no_anchor",
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.request_model) if state.request_model else None,
                    owner_check_applied=True,
                )
                state.force_durable_takeover = True
            else:
                _log_http_bridge_event(
                    "owner_mismatch_local_recover",
                    state.key,
                    account_id=None,
                    model=state.request_model,
                    detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_no_ring",
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.request_model) if state.request_model else None,
                    owner_check_applied=True,
                )
                state.force_durable_takeover = True
        else:
            assert state.owner_instance is not None
            state.owner_endpoint = await self._ring_membership.resolve_endpoint(state.owner_instance)
            if state.owner_endpoint is None:
                if _http_bridge_has_durable_recovery_anchor(
                    previous_response_id=state.previous_response_id, durable_lookup=state.durable_lookup
                ):
                    if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                        bridge_durable_recover_total.labels(path="owner_missing").inc()
                    _log_http_bridge_event(
                        "owner_endpoint_missing_local_recover",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=True,
                    )
                    state.force_durable_takeover = True
                else:
                    _log_http_bridge_event(
                        "owner_mismatch_local_recover",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_no_endpoint",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=True,
                    )
                    state.force_durable_takeover = True
            elif _http_bridge_endpoint_matches_current_instance(state.owner_endpoint, state.settings):
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _log_http_bridge_event(
                    "owner_mismatch_local_recover",
                    state.key,
                    account_id=None,
                    model=state.request_model,
                    detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_same_endpoint",
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.request_model) if state.request_model else None,
                    owner_check_applied=True,
                )
                state.force_durable_takeover = True
            else:
                state.owner_forward = _HTTPBridgeOwnerForward(
                    owner_instance=state.owner_instance, owner_endpoint=state.owner_endpoint, key=state.key
                )

    async def resolve_bridge_owner(self, state: BridgeSessionAcquisition) -> None:
        if state.owner_check_required or state.key.affinity_kind == "prompt_cache":
            state.owner_instance = _durable_bridge_lookup_active_owner(state.durable_lookup)
            state.hard_continuity_lookup = state.owner_check_required or state.previous_response_id is not None
            state.ring_lookup_failed = False
            if state.owner_instance is None:
                try:
                    state.owner_instance = await _http_bridge_owner_instance(
                        state.key, state.settings, self._ring_membership
                    )
                except Exception as exc:
                    state.ring_lookup_failed = True
                    if state.hard_continuity_lookup:
                        _record_continuity_fail_closed(
                            surface="http_bridge",
                            reason="owner_metadata_unavailable",
                            previous_response_id=state.previous_response_id,
                            session_id=state.incoming_turn_state or state.incoming_session_key,
                            upstream_error_code="owner_lookup_failed",
                        )
                        raise ProxyResponseError(502, _http_bridge_owner_lookup_unavailable_error_envelope()) from exc
                    if _http_bridge_can_local_recover_without_ring(
                        key=state.key,
                        headers=state.headers,
                        previous_response_id=state.previous_response_id,
                        durable_lookup=state.durable_lookup,
                    ):
                        logger.warning("Bridge owner lookup failed; allowing local recovery path", exc_info=True)
                        state.owner_instance = state.settings.http_responses_session_bridge_instance_id
                    else:
                        raise
            try:
                state.current_instance, state.ring = await _active_http_bridge_instance_ring(
                    state.settings, self._ring_membership
                )
            except Exception as exc:
                if state.hard_continuity_lookup:
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="owner_metadata_unavailable",
                        previous_response_id=state.previous_response_id,
                        session_id=state.incoming_turn_state or state.incoming_session_key,
                        upstream_error_code="ring_lookup_failed",
                    )
                    raise ProxyResponseError(502, _http_bridge_owner_lookup_unavailable_error_envelope()) from exc
                if state.ring_lookup_failed or _http_bridge_can_local_recover_without_ring(
                    key=state.key,
                    headers=state.headers,
                    previous_response_id=state.previous_response_id,
                    durable_lookup=state.durable_lookup,
                ):
                    logger.warning("Bridge ring lookup failed; falling back to local recovery ring", exc_info=True)
                    state.current_instance = state.settings.http_responses_session_bridge_instance_id
                    state.ring = (state.current_instance,)
                else:
                    raise
            state.owner_mismatch = state.owner_instance is not None and state.owner_instance != state.current_instance
            if state.owner_mismatch and (len(state.ring) > 1 or state.durable_lookup is not None):
                if PROMETHEUS_AVAILABLE and bridge_owner_mismatch_total is not None:
                    bridge_owner_mismatch_total.labels(strength=_http_bridge_key_strength(state.key)).inc()
                if (
                    state.owner_check_required
                    and (not (state.previous_response_id is not None and state.allow_previous_response_recovery_rebind))
                    and (not state.allow_bootstrap_owner_rebind)
                ):
                    _log_http_bridge_event(
                        "owner_mismatch",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=forward",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=True,
                    )
                    if state.allow_forward_to_owner:
                        await self.resolve_bridge_owner_forward(state)
                    elif _http_bridge_has_durable_recovery_anchor(
                        previous_response_id=state.previous_response_id, durable_lookup=state.durable_lookup
                    ):
                        if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                            bridge_durable_recover_total.labels(path="owner_missing").inc()
                        _log_http_bridge_event(
                            "owner_mismatch_local_recover",
                            state.key,
                            account_id=None,
                            model=state.request_model,
                            detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover",
                            cache_key_family=state.key.affinity_kind,
                            model_class=_extract_model_class(state.request_model) if state.request_model else None,
                            owner_check_applied=True,
                        )
                        state.force_durable_takeover = True
                    else:
                        _log_http_bridge_event(
                            "owner_mismatch_local_recover",
                            state.key,
                            account_id=None,
                            model=state.request_model,
                            detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_no_forward",
                            cache_key_family=state.key.affinity_kind,
                            model_class=_extract_model_class(state.request_model) if state.request_model else None,
                            owner_check_applied=True,
                        )
                        state.force_durable_takeover = True
                else:
                    _log_http_bridge_event(
                        "prompt_cache_locality_miss",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_rebind",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=False,
                    )
                    if _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor(
                        key=state.key,
                        owner_instance=state.owner_instance,
                        current_instance=state.current_instance,
                        ring=state.ring,
                    ):
                        state.force_durable_takeover = True
                    elif state.allow_previous_response_recovery_rebind or state.allow_bootstrap_owner_rebind:
                        state.force_durable_takeover = True
                    _log_http_bridge_event(
                        "soft_locality_rebind",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_rebind",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=False,
                    )
                    if PROMETHEUS_AVAILABLE:
                        if bridge_prompt_cache_locality_miss_total is not None:
                            bridge_prompt_cache_locality_miss_total.inc()
                        if bridge_soft_local_rebind_total is not None:
                            bridge_soft_local_rebind_total.inc()
                        if bridge_local_rebind_total is not None:
                            bridge_local_rebind_total.labels(reason="prompt_cache_locality_miss").inc()
