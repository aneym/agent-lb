from __future__ import annotations

import asyncio
import inspect

from app.modules.proxy._service.http_bridge.acquisition_state import BridgeSessionAcquisition
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_allow_durable_takeover,
    _http_bridge_startup_wait_timeout_error,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.operation_contract import BridgeOperationsProtocol
from app.modules.proxy._service.support import _HTTPBridgeSession
from app.modules.proxy.affinity import _extract_model_class


class BridgeSessionRegistrationMixin(BridgeOperationsProtocol):
    async def create_and_register_bridge_session(self, state: BridgeSessionAcquisition) -> _HTTPBridgeSession:
        state.created_session = None
        state.session_registered = False
        state.require_preferred_account = (
            state.previous_response_id is not None
            and state.preferred_account_id is not None
            or (state.preferred_account_id is not None and (not state.fallback_on_preferred_account_unavailable))
        )
        try:
            state.create_session = self._create_http_bridge_session
            state.create_kwargs = {
                "headers": state.headers,
                "affinity": state.affinity,
                "api_key": state.api_key,
                "request_model": state.request_model,
                "idle_ttl_seconds": state.effective_idle_ttl_seconds,
                "response_create_concurrency": state.response_create_concurrency,
                "request_stage": state.request_stage,
                "preferred_account_id": state.preferred_account_id,
                "require_preferred_account": state.require_preferred_account,
                "fallback_on_preferred_account_unavailable": state.fallback_on_preferred_account_unavailable,
                "request_usage_budget": state.request_usage_budget,
            }
            try:
                state.create_signature = inspect.signature(state.create_session)
            except (TypeError, ValueError):
                state.create_signature = None
            state.create_accepts_var_keyword = state.create_signature is not None and any(
                (
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in state.create_signature.parameters.values()
                )
            )
            if state.create_signature is not None and (not state.create_accepts_var_keyword):
                state.create_kwargs = {
                    name: value
                    for name, value in state.create_kwargs.items()
                    if name in state.create_signature.parameters
                }
            state.created_session = await state.create_session(state.key, **state.create_kwargs)
            await self._claim_durable_http_bridge_session(
                state.created_session,
                allow_takeover=state.force_durable_takeover
                or _http_bridge_allow_durable_takeover(state.durable_lookup),
                force_owner_epoch_advance=state.force_durable_takeover,
            )
            async with self._http_bridge_lock:
                state.current_future = self._http_bridge_inflight_sessions.get(state.key)
                if state.current_future is state.inflight_future:
                    self._http_bridge_inflight_sessions.pop(state.key, None)
                    self._http_bridge_sessions[state.key] = state.created_session
                    state.session_registered = True
                    if state.inflight_future is not None and (not state.inflight_future.done()):
                        state.inflight_future.set_result(state.created_session)
            if not state.session_registered:
                raise _http_bridge_startup_wait_timeout_error(
                    "http_bridge_session_registration", code="capacity_exhausted_active_sessions"
                )
        except BaseException as exc:
            async with self._http_bridge_lock:
                state.current_future = self._http_bridge_inflight_sessions.get(state.key)
                if state.current_future is state.inflight_future:
                    self._http_bridge_inflight_sessions.pop(state.key, None)
                    if state.inflight_future is not None and (not state.inflight_future.done()):
                        if isinstance(exc, asyncio.CancelledError):
                            state.inflight_future.cancel()
                        else:
                            state.inflight_future.set_exception(exc)
                            state.inflight_future.exception()
            if state.created_session is not None and (not state.session_registered):
                await self._close_http_bridge_session(state.created_session)
            raise
        assert state.created_session is not None
        _log_http_bridge_event(
            "create",
            state.key,
            account_id=state.created_session.account.id,
            model=state.created_session.request_model,
            detail=f"request_stage={state.request_stage}, preferred_account_id={state.preferred_account_id}, selected_account_id={state.created_session.account.id}, durable_session_id={state.created_session.durable_session_id}",
            cache_key_family=state.key.affinity_kind,
            model_class=_extract_model_class(state.created_session.request_model)
            if state.created_session.request_model
            else None,
        )
        if state.old_account_id is not None and state.old_account_id != state.created_session.account.id:
            _log_http_bridge_event(
                "reallocation_orphan",
                state.key,
                account_id=state.created_session.account.id,
                model=state.created_session.request_model,
                detail=f"old_account={state.old_account_id}",
                cache_key_family=state.key.affinity_kind,
                model_class=_extract_model_class(state.created_session.request_model)
                if state.created_session.request_model
                else None,
            )
        return state.created_session
