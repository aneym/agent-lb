from __future__ import annotations

from typing import Any, Literal

from app.core.clients.proxy import ProxyResponseError
from app.core.utils.sse import format_sse_event
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_precreated_retry_failure_error,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.request_preparation import logger
from app.modules.proxy._service.http_bridge.service_stubs import (
    _classify_upstream_close,
    _prepare_websocket_request_state_for_auth_replay,
    _prepare_websocket_request_state_for_visible_output_replay,
    _security_work_advisory_event,
    _service_time,
    _websocket_auth_failure_permanent_code,
    _websocket_auth_failure_requires_reauth,
)
from app.modules.proxy._service.support import (
    _clear_websocket_request_error_overrides,
    _HTTPBridgeSession,
    _websocket_request_can_replay_before_visible_output,
    _WebSocketRequestState,
)
from app.modules.proxy.affinity import _extract_model_class
from app.modules.proxy.helpers import _normalize_error_code, _parse_openai_error

_WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE = "account_auth_invalidated"

_NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE = "no_security_work_authorized_accounts"

_SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work, but no account is marked as authorized for "
    "security work. agent-lb is continuing with normal account selection; the upstream request may still fail until "
    "an account with Trusted Access for Cyber is marked as security-work-authorized."
)



class HTTPBridgeRequestRetryMixin:
    async def _retry_http_bridge_request_on_fresh_upstream(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
        send_request: bool = True,
    ) -> bool:
        retry_text_data = text_data
        if request_state.previous_response_id is not None and send_request:
            if (
                not request_state.proxy_injected_previous_response_id
                or not request_state.fresh_upstream_request_text
                or not request_state.fresh_upstream_request_is_retry_safe
            ):
                return False
            retry_text_data = request_state.fresh_upstream_request_text
        if request_state.replay_count >= 1:
            return False
        if request_state.response_event_count > 0:
            return False
        request_state.replay_count += 1
        _log_http_bridge_event(
            "retry_fresh_upstream",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=1,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(
                session,
                request_state=request_state,
                restart_reader=True,
            )
            if send_request:
                if retry_text_data != text_data:
                    request_state.previous_response_id = None
                    request_state.proxy_injected_previous_response_id = False
                    request_state.request_text = retry_text_data
                await session.upstream.send_text(retry_text_data)
            _clear_websocket_request_error_overrides(request_state)
            session.last_used_at = _service_time().monotonic()
            return True
        except Exception:
            logger.warning("HTTP bridge retry on fresh upstream failed", exc_info=True)
            return False


    async def _retry_http_bridge_precreated_request(self: Any, session: "_HTTPBridgeSession") -> bool:
        async with session.pending_lock:
            retryable_requests = [
                request_state
                for request_state in session.pending_requests
                if not request_state.draining_until_terminal
                and _websocket_request_can_replay_before_visible_output(request_state)
            ]
            if len(retryable_requests) != 1:
                return False
            request_state = retryable_requests[0]
            if request_state.previous_response_id is not None and not (
                request_state.proxy_injected_previous_response_id
                and request_state.fresh_upstream_request_is_retry_safe
                and request_state.fresh_upstream_request_text
            ):
                return False
            close_classification = _classify_upstream_close(
                session.last_upstream_close_code,
                response_events_seen=request_state.response_event_count,
            )
            if close_classification == "rejected":
                request_state.error_code_override = "upstream_rejected_input"
                request_state.error_http_status_override = 502
                request_state.error_message_override = (
                    "Upstream rejected the request before response.created "
                    f"(close_code={session.last_upstream_close_code})"
                )
                return False
            request_text = _prepare_websocket_request_state_for_visible_output_replay(request_state)
            if request_text is None:
                return False
        _log_http_bridge_event(
            "retry_precreated",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=1,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(session, request_state=request_state)
            await session.upstream.send_text(request_text)
            session.last_used_at = _service_time().monotonic()
            return True
        except Exception as exc:
            request_state.error_code_override, request_state.error_message_override = (
                _http_bridge_precreated_retry_failure_error(exc)
            )
            if isinstance(exc, ProxyResponseError):
                logger.info(
                    "HTTP bridge pre-created retry failed with terminal proxy error code=%s message=%s",
                    request_state.error_code_override,
                    request_state.error_message_override,
                )
            else:
                logger.warning("HTTP bridge pre-created retry failed", exc_info=True)
            return False


    async def _retry_http_bridge_precreated_auth_request(
        self: Any,
        session: "_HTTPBridgeSession",
        request_state: _WebSocketRequestState,
        *,
        error_message: str | None,
    ) -> Literal["not_replayable", "retried", "failed"]:
        permanent_failure_code = _websocket_auth_failure_permanent_code(error_message)
        request_text = _prepare_websocket_request_state_for_auth_replay(request_state)
        if request_text is None:
            await self._load_balancer.mark_permanent_failure(session.account, permanent_failure_code)
            setattr(request_state, "account_health_error_handled", True)
            request_state.force_refresh_account_id = None
            request_state.preferred_account_id = None
            request_state.excluded_account_ids.add(session.account.id)
            return "not_replayable"

        if _websocket_auth_failure_requires_reauth(error_message):
            failure_code = permanent_failure_code
        elif request_state.auth_replay_counts_by_account.get(session.account.id, 0) == 0:
            failure_code = None
            request_state.auth_replay_counts_by_account[session.account.id] = 1
            request_state.force_refresh_account_id = session.account.id
            request_state.preferred_account_id = session.account.id
        else:
            failure_code = _WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE

        if failure_code is not None:
            await self._load_balancer.mark_permanent_failure(session.account, failure_code)
            request_state.force_refresh_account_id = None
            request_state.preferred_account_id = None
            request_state.excluded_account_ids.add(session.account.id)

        async with session.pending_lock:
            if request_state not in session.pending_requests:
                session.pending_requests.appendleft(request_state)
                session.queued_request_count += 1

        _log_http_bridge_event(
            "retry_precreated_auth",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=await self._http_bridge_pending_count(session),
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(session, request_state=request_state)
            await session.upstream.send_text(request_text)
            session.last_used_at = _service_time().monotonic()
            return "retried"
        except Exception as exc:
            request_state.error_code_override, request_state.error_message_override = (
                _http_bridge_precreated_retry_failure_error(exc)
            )
            if isinstance(exc, ProxyResponseError):
                logger.info(
                    "HTTP bridge pre-created auth retry failed with terminal proxy error code=%s message=%s",
                    request_state.error_code_override,
                    request_state.error_message_override,
                )
            else:
                logger.warning("HTTP bridge pre-created auth retry failed", exc_info=True)
            return "failed"


    async def _retry_http_bridge_security_work_request(
        self: Any,
        session: "_HTTPBridgeSession",
        request_state: _WebSocketRequestState,
    ) -> bool:
        if session.account.security_work_authorized:
            return False
        if request_state.response_id is not None:
            return False
        if request_state.replay_count >= 1:
            return False
        retry_text = request_state.request_text
        if not retry_text:
            return False
        if request_state.previous_response_id is not None:
            if not (
                request_state.fresh_upstream_request_text is not None
                and request_state.fresh_upstream_request_is_retry_safe
            ):
                return False
            retry_text = request_state.fresh_upstream_request_text

        request_state.replay_count += 1
        request_state.response_id = None
        request_state.awaiting_response_created = True
        if retry_text != request_state.request_text:
            request_state.previous_response_id = None
            request_state.proxy_injected_previous_response_id = False
            request_state.request_text = retry_text

        async with session.pending_lock:
            if request_state not in session.pending_requests:
                session.pending_requests.append(request_state)
                session.queued_request_count += 1

        _log_http_bridge_event(
            "retry_security_work_authorized",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            pending_count=await self._http_bridge_pending_count(session),
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
        try:
            await self._reconnect_http_bridge_session(
                session,
                request_state=request_state,
                require_security_work_authorized=True,
            )
            await session.upstream.send_text(retry_text)
            session.last_used_at = _service_time().monotonic()
            return True
        except Exception as exc:
            logger.warning("HTTP bridge security-work retry failed", exc_info=True)
            if isinstance(exc, ProxyResponseError):
                error = _parse_openai_error(exc.payload)
                code = _normalize_error_code(error.code if error else None, error.type if error else None)
                if code == _NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE and request_state.event_queue is not None:
                    await request_state.event_queue.put(
                        format_sse_event(
                            _security_work_advisory_event(
                                code=_NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE,
                                message=_SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE,
                                request_id=request_state.request_log_id or request_state.request_id,
                                action="forward_original_security_work_error",
                            )
                        )
                    )
            async with session.pending_lock:
                if request_state in session.pending_requests:
                    session.pending_requests.remove(request_state)
                    session.queued_request_count = max(0, session.queued_request_count - 1)
            return False
