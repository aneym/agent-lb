from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, TypeVar

import anyio

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.modules.proxy._service.http_bridge.helpers import _http_bridge_key_strength, _log_http_bridge_event
from app.modules.proxy._service.http_bridge.request_cleanup import HTTPBridgeRequestCleanupMixin
from app.modules.proxy._service.http_bridge.request_preparation import HTTPBridgeRequestPreparationMixin, logger
from app.modules.proxy._service.http_bridge.request_retry import HTTPBridgeRequestRetryMixin
from app.modules.proxy._service.http_bridge.service_stubs import _service_time
from app.modules.proxy._service.support import (
    _copy_websocket_route_metadata_from_session,
    _HTTPBridgeSession,
    _WebSocketRequestState,
)
from app.modules.proxy.affinity import _extract_model_class

T = TypeVar("T")
_TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
_UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY = frozenset({1011})
_SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE = "security_work_authorization_required"
_SECURITY_WORK_RETRY_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work. "
    "agent-lb is retrying on an account marked as authorized for security work."
)
_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS = 5.0
_HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD = 100


class _HTTPBridgeRequestSubmitMixin(HTTPBridgeRequestPreparationMixin, HTTPBridgeRequestCleanupMixin, HTTPBridgeRequestRetryMixin):
    async def _submit_http_bridge_request(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        queue_limit: int,
    ) -> None:
        if request_state.response_id is not None or request_state.response_event_count > 0:
            _log_http_bridge_event(
                "submit_after_response_event",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail=(
                    f"response_id={request_state.response_id}, "
                    f"response_events_seen={request_state.response_event_count}"
                ),
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )
            raise ProxyResponseError(
                502,
                openai_error(
                    "upstream_unavailable",
                    "HTTP responses session bridge request already has upstream response events",
                    error_type="server_error",
                ),
            )
        if session.closed:
            async with session.lifecycle_lock:
                if session.closed:
                    current_session = session
                    http_bridge_sessions = getattr(self, "_http_bridge_sessions", None)
                    bridge_lock = getattr(self, "_http_bridge_lock", None)
                    if bridge_lock is not None:
                        async with bridge_lock:
                            if http_bridge_sessions is not None:
                                current_session = http_bridge_sessions.get(session.key)
                    elif http_bridge_sessions is not None:
                        current_session = http_bridge_sessions.get(session.key)
                    if current_session is None and _http_bridge_key_strength(session.key) == "hard":
                        _log_http_bridge_event(
                            "submit_on_closed",
                            session.key,
                            account_id=session.account.id,
                            model=session.request_model,
                            detail="session_unregistered_before_reconnect",
                            cache_key_family=session.key.affinity_kind,
                            model_class=_extract_model_class(session.request_model) if session.request_model else None,
                        )
                        raise ProxyResponseError(
                            502,
                            openai_error("upstream_unavailable", "HTTP responses session bridge is closed"),
                        )
                    if current_session is not session:
                        _log_http_bridge_event(
                            "submit_on_closed",
                            session.key,
                            account_id=session.account.id,
                            model=session.request_model,
                            detail="session_replaced_before_reconnect",
                            cache_key_family=session.key.affinity_kind,
                            model_class=_extract_model_class(session.request_model) if session.request_model else None,
                        )

                    recovered = await self._retry_http_bridge_request_on_fresh_upstream(
                        session,
                        request_state=request_state,
                        text_data=text_data,
                        send_request=False,
                    )
                    if recovered:
                        session.closed = False
                    else:
                        _log_http_bridge_event(
                            "submit_on_closed",
                            session.key,
                            account_id=session.account.id,
                            model=session.request_model,
                            cache_key_family=session.key.affinity_kind,
                            model_class=_extract_model_class(session.request_model) if session.request_model else None,
                        )
                        raise ProxyResponseError(
                            502,
                            openai_error("upstream_unavailable", "HTTP responses session bridge is closed"),
                        )
        if session.upstream_control.retire_after_drain:
            await self._retire_http_bridge_after_drain_if_ready(session)
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "HTTP responses session bridge is retiring"),
            )
        await self._maybe_prewarm_http_bridge_session(
            session,
            request_state=request_state,
            text_data=text_data,
        )
        gate_acquired = False
        request_enqueued = False
        async with session.pending_lock:
            if session.queued_request_count >= queue_limit:
                _log_http_bridge_event(
                    "bridge_queue_full",
                    session.key,
                    account_id=session.account.id,
                    model=session.request_model,
                    pending_count=session.queued_request_count,
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                raise ProxyResponseError(
                    429,
                    openai_error(
                        "bridge_queue_full",
                        "HTTP responses session bridge queue is full",
                        error_type="rate_limit_error",
                    ),
                )
            session.queued_request_count += 1
        try:
            text_data = await self._inline_http_bridge_image_urls(text_data, request_state)
            self._start_request_state_api_key_reservation_heartbeat(
                request_state,
                api_key=request_state.api_key,
                surface="http_bridge",
            )
            _copy_websocket_route_metadata_from_session(request_state, session)
            await self._acquire_request_state_response_create_admission(
                request_state,
                response_create_gate=session.response_create_gate,
                account_id=session.account.id,
                surface="http_bridge",
                bridge_session=session,
            )
            gate_acquired = True
            async with session.lifecycle_lock:
                current_session = session
                http_bridge_sessions = getattr(self, "_http_bridge_sessions", None)
                bridge_lock = getattr(self, "_http_bridge_lock", None)
                if bridge_lock is not None:
                    async with bridge_lock:
                        if http_bridge_sessions is not None:
                            current_session = http_bridge_sessions.get(session.key)
                elif http_bridge_sessions is not None:
                    current_session = http_bridge_sessions.get(session.key)
                session_unregistered = current_session is None and _http_bridge_key_strength(session.key) == "hard"
                session_replaced = current_session is not None and current_session is not session
                if session.closed or session_unregistered or session_replaced:
                    _log_http_bridge_event(
                        "submit_on_closed",
                        session.key,
                        account_id=session.account.id,
                        model=session.request_model,
                        detail=(
                            "session_retired_after_admission"
                            if session.closed
                            else (
                                "session_unregistered_after_admission"
                                if session_unregistered
                                else "session_replaced_after_admission"
                            )
                        ),
                        cache_key_family=session.key.affinity_kind,
                        model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    )
                    raise ProxyResponseError(
                        502,
                        openai_error("upstream_unavailable", "HTTP responses session bridge is closed"),
                    )
                async with session.pending_lock:
                    session.pending_requests.append(request_state)
                request_enqueued = True
                await session.upstream.send_text(text_data)
                session.last_used_at = _service_time().monotonic()
        except ProxyResponseError:
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
                counted_in_queue=True,
            )
            raise
        except asyncio.CancelledError:
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
                counted_in_queue=True,
            )
            raise
        except Exception as exc:
            _log_http_bridge_event(
                "send_failure",
                session.key,
                account_id=session.account.id,
                model=session.request_model,
                detail=str(exc) or None,
                cache_key_family=session.key.affinity_kind,
                model_class=_extract_model_class(session.request_model) if session.request_model else None,
            )
            retried = await self._retry_http_bridge_request_on_fresh_upstream(
                session,
                request_state=request_state,
                text_data=text_data,
            )
            if retried:
                return
            await self._cleanup_http_bridge_submit_interruption(
                session,
                request_state=request_state,
                gate_acquired=gate_acquired,
                request_enqueued=request_enqueued,
                counted_in_queue=True,
            )
            await self._fail_pending_websocket_requests(
                account=session.account,
                account_id_value=session.account.id,
                pending_requests=deque([request_state]),
                pending_lock=anyio.Lock(),
                error_code="stream_incomplete",
                error_message="Upstream websocket closed before response.completed",
                api_key=None,
                response_create_gate=session.response_create_gate,
            )
            session.closed = True
            try:
                await session.upstream.close()
            except Exception:
                logger.debug("Failed to close HTTP bridge upstream websocket after send failure", exc_info=True)

            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", str(exc) or "Upstream websocket closed"),
            ) from exc
