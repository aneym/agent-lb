from __future__ import annotations

from typing import Any

from app.core import shutdown as shutdown_state
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_request_counts_against_queue,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.request_preparation import logger
from app.modules.proxy._service.http_bridge.service_stubs import (
    _release_websocket_response_create_gate,
    _service_get_settings,
)
from app.modules.proxy._service.support import _HTTPBridgeSession, _WebSocketRequestState
from app.modules.proxy.affinity import _extract_model_class


class HTTPBridgeRequestCleanupMixin:
    async def _cleanup_http_bridge_submit_interruption(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        gate_acquired: bool,
        request_enqueued: bool,
        counted_in_queue: bool,
    ) -> None:
        async with session.pending_lock:
            if request_enqueued and request_state in session.pending_requests:
                session.pending_requests.remove(request_state)
            if counted_in_queue:
                session.queued_request_count = max(0, session.queued_request_count - 1)
        self._cancel_request_state_api_key_reservation_heartbeat(request_state)
        if gate_acquired:
            await _release_websocket_response_create_gate(request_state, session.response_create_gate)


    async def _detach_http_bridge_request(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
    ) -> bool:
        detached = False
        async with session.pending_lock:
            if request_state in session.pending_requests and not request_state.draining_until_terminal:
                request_state.draining_until_terminal = True
                request_state.downstream_visible = False
                session.queued_request_count = max(0, session.queued_request_count - 1)
                session.upstream_control.reconnect_requested = True
                session.upstream_control.retire_after_drain = True
                detached = True
        request_state.event_queue = None

        await _release_websocket_response_create_gate(request_state, session.response_create_gate)
        if not detached:
            return False
        self._cancel_request_state_api_key_reservation_heartbeat(request_state)
        await self._release_websocket_request_state_reservation(request_state)
        request_state.api_key_reservation = None
        await self._retire_http_bridge_after_drain_if_ready(session)
        return True


    async def _retire_http_bridge_after_drain_if_ready(self: Any, session: "_HTTPBridgeSession") -> bool:
        if not (session.upstream_control.reconnect_requested and session.upstream_control.retire_after_drain):
            return False
        async with session.pending_lock:
            has_visible_pending = any(
                _http_bridge_request_counts_against_queue(request_state) for request_state in session.pending_requests
            )
            should_reconnect = not has_visible_pending and session.queued_request_count == 0
            if should_reconnect:
                session.pending_requests.clear()
        if not should_reconnect:
            return False

        await self._close_http_bridge_session(session)
        return True


    async def _retire_stale_pending_http_bridge_session(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        detail: str,
    ) -> None:
        session.closed = True
        async with self._http_bridge_lock:
            if self._http_bridge_sessions.get(session.key) is session:
                self._http_bridge_sessions.pop(session.key, None)
                self._unregister_http_bridge_turn_states_locked(session)
                self._unregister_http_bridge_previous_response_ids_locked(session)
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            durable_session_id = session.durable_session_id
            durable_owner_epoch = session.durable_owner_epoch
            session.durable_session_id = None
            session.durable_owner_epoch = None
            try:
                await self._durable_bridge.release_live_session(
                    session_id=durable_session_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=durable_owner_epoch,
                    draining=shutdown_state.is_bridge_drain_active(),
                )
            except Exception:
                session.durable_session_id = durable_session_id
                session.durable_owner_epoch = durable_owner_epoch
                logger.warning("Failed to release stale pending HTTP bridge session lease", exc_info=True)
        await self._load_balancer.release_account_lease(session.account_lease)
        session.account_lease = None
        if not session.upstream_close_attempted:
            session.upstream_close_attempted = True
            try:
                await session.upstream.close()
            except Exception:
                logger.debug("Failed to close stale pending HTTP bridge upstream websocket", exc_info=True)
        _log_http_bridge_event(
            "retire_stale_pending",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=await self._http_bridge_pending_count(session),
            detail=detail,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
