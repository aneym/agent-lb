from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping as MappingABC
from typing import Any, AsyncIterator, Mapping, cast

from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import response_failed_event
from app.core.identity import get_request_identity
from app.core.openai.requests import ResponsesRequest
from app.core.utils.request_id import ensure_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import StickySessionKind
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy._service.observability import _maybe_log_proxy_request_shape, _record_continuity_fail_closed
from app.modules.proxy._service.streaming.protocol import _StreamingServiceProtocol
from app.modules.proxy._service.streaming.retry_affinity import resolve_retry_affinity
from app.modules.proxy._service.streaming.retry_prepare import prepare_stream_attempt
from app.modules.proxy._service.streaming.retry_recovery import recover_stream_response_error
from app.modules.proxy._service.streaming.retry_selection import select_retry_account
from app.modules.proxy._service.streaming.retry_state import RetryState, release_tracked_stream_lease, retry_facade
from app.modules.proxy._service.support import (
    _request_log_useragent_fields,
    _RetryableStreamError,
    _stream_settlement_error_payload,
    _StreamSettlement,
    _TerminalStreamError,
    _TransientStreamError,
    _WebSocketUpstreamControl,
)
from app.modules.proxy.affinity import (
    _owner_lookup_session_id_from_headers,
    _prompt_cache_key_from_request_model,
    _sticky_key_for_responses_request,
)
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.helpers import (
    _apply_error_metadata,
    _normalize_error_code,
    _parse_openai_error,
    _upstream_error_from_openai,
)

REQUEST_TRANSPORT_HTTP = "http"


def protocol_only_stream_event_type(line: str) -> str | None:
    payload = parse_sse_data_json(line)
    if payload is None:
        return None
    event_type = payload.get("type")
    return event_type if isinstance(event_type, str) else None


def protocol_only_stream_error(line: str) -> tuple[str | None, str | None]:
    payload = parse_sse_data_json(line)
    if payload is None:
        return None, None
    event_type = payload.get("type")
    error_value: Any = None
    if event_type == "response.failed":
        response_value = payload.get("response")
        if isinstance(response_value, MappingABC):
            error_value = response_value.get("error")
    elif event_type == "error":
        error_value = payload.get("error")
        if not isinstance(error_value, MappingABC):
            error_value = payload
    if not isinstance(error_value, MappingABC):
        return None, None
    code_value = error_value.get("code")
    type_value = error_value.get("type")
    message_value = error_value.get("message")
    code = _normalize_error_code(
        code_value if isinstance(code_value, str) else None,
        type_value if isinstance(type_value, str) else None,
    )
    message = message_value if isinstance(message_value, str) else None
    return code, message


class _StreamingRetryMixin:
    async def _stream_with_retry(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        codex_session_affinity: bool,
        propagate_http_errors: bool,
        openai_cache_affinity: bool,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        suppress_text_done_events: bool,
        request_transport: str,
        rewritten_file_account_id: str | None = None,
        upstream_stream_transport_override: str | None = None,
        retry_protocol_only_failures: bool = False,
    ) -> AsyncIterator[str]:
        state = RetryState()
        state.RetryableStreamError = _RetryableStreamError
        state.StreamSettlement = _StreamSettlement
        state.TransientStreamError = _TransientStreamError
        state.WebSocketUpstreamControl = _WebSocketUpstreamControl
        state.apply_error_metadata = _apply_error_metadata
        state.normalize_error_code = _normalize_error_code
        state.owner_lookup_session_id_from_headers = _owner_lookup_session_id_from_headers
        state.parse_openai_error = _parse_openai_error
        state.record_continuity_fail_closed = _record_continuity_fail_closed
        state.stream_settlement_error_payload = _stream_settlement_error_payload
        state.upstream_error_from_openai = _upstream_error_from_openai
        state.protocol_only_stream_error = protocol_only_stream_error
        state.protocol_only_stream_event_type = protocol_only_stream_event_type
        state.payload = payload
        state.headers = headers
        state.codex_session_affinity = codex_session_affinity
        state.propagate_http_errors = propagate_http_errors
        state.openai_cache_affinity = openai_cache_affinity
        state.api_key = api_key
        state.api_key_reservation = api_key_reservation
        state.suppress_text_done_events = suppress_text_done_events
        state.request_transport = request_transport
        state.rewritten_file_account_id = rewritten_file_account_id
        state.upstream_stream_transport_override = upstream_stream_transport_override
        state.retry_protocol_only_failures = retry_protocol_only_failures
        state.proxy = cast(_StreamingServiceProtocol, self)
        state.useragent, state.useragent_group = _request_log_useragent_fields(state.headers)
        state.request_id = ensure_request_id()
        state.request_identity = get_request_identity()
        state.start = time.monotonic()
        state.base_settings = retry_facade().get_settings()
        state.settings = await retry_facade().get_settings_cache().get()
        state.deadline = state.start + retry_facade()._stream_request_budget_seconds(
            state.base_settings,
            request_transport=state.request_transport,
        )
        state.prefer_earlier_reset = state.settings.prefer_earlier_reset_accounts
        state.upstream_stream_transport = state.upstream_stream_transport_override
        if state.upstream_stream_transport is None:
            state.upstream_stream_transport = retry_facade()._resolve_upstream_stream_transport(
                state.settings.upstream_stream_transport
            )
        if state.request_transport == REQUEST_TRANSPORT_HTTP and state.upstream_stream_transport == "websocket":
            state.upstream_stream_transport = "http"
        if state.rewritten_file_account_id is None:
            state.proxy._raise_for_unsupported_input_image_references(state.payload)
            state.rewritten_file_account_id = await state.proxy._resolve_file_account_for_responses(
                state.payload, state.headers
            )
        state.had_prompt_cache_key = _prompt_cache_key_from_request_model(state.payload) is not None
        state.affinity = _sticky_key_for_responses_request(
            state.payload,
            state.headers,
            codex_session_affinity=state.codex_session_affinity,
            openai_cache_affinity=state.openai_cache_affinity,
            openai_cache_affinity_max_age_seconds=state.settings.openai_cache_affinity_max_age_seconds,
            sticky_threads_enabled=state.settings.sticky_threads_enabled,
            api_key=state.api_key,
        )
        state.sticky_key_source = "none"
        if state.affinity.kind == StickySessionKind.CODEX_SESSION:
            state.sticky_key_source = "session_header"
        elif state.affinity.key:
            state.sticky_key_source = "payload" if state.had_prompt_cache_key else "derived"
        _maybe_log_proxy_request_shape(
            "stream",
            state.payload,
            state.headers,
            sticky_kind=state.affinity.kind.value if state.affinity.kind is not None else None,
            sticky_key_source=state.sticky_key_source,
            prompt_cache_key_set=_prompt_cache_key_from_request_model(state.payload) is not None,
        )
        state.routing_strategy = retry_facade()._routing_strategy(state.settings)
        state.max_attempts = retry_facade()._STREAM_MAX_ACCOUNT_ATTEMPTS
        state.settled = False
        state.any_attempt_logged = False
        state.settlement = _StreamSettlement()
        state.last_transient_exc = None
        state.last_security_work_retry_error = None
        state.excluded_account_ids = set()
        state.preferred_account_id = None
        state.file_preferred_account_id = state.rewritten_file_account_id
        state.require_preferred_account = False
        state.last_retryable_stream_error = None
        state.require_security_work_authorized = False
        state.account_leases = []
        state.estimated_lease_tokens = retry_facade()._estimated_lease_tokens_from_request_usage_budget(
            estimate_api_key_request_usage(state.payload)
        )

        try:
            async for event in resolve_retry_affinity(state):
                yield event
            if state.stop:
                return
            for state.attempt in range(state.max_attempts):
                async for event in select_retry_account(state):
                    yield event
                if state.stop:
                    return
                try:
                    async for event in prepare_stream_attempt(state):
                        yield event
                    if state.stop:
                        return
                    continue
                except _RetryableStreamError as exc:
                    if retry_facade()._is_security_work_authorization_required_error(
                        exc.code, exc.error.get("message")
                    ):
                        if (
                            state.account.security_work_authorized
                            or state.account.id == state.file_preferred_account_id
                            or state.require_preferred_account
                            or state.attempt >= state.max_attempts - 1
                        ):
                            state.event = response_failed_event(
                                exc.code,
                                exc.error.get("message") or "Security work authorization is required",
                                response_id=state.request_id,
                            )
                            yield format_sse_event(state.event)
                            return
                        retry_facade().logger.info(
                            "Retrying on security-work-authorized account request_id=%s account_id=%s",
                            state.request_id,
                            state.account.id,
                        )
                        yield format_sse_event(
                            retry_facade()._security_work_advisory_event(
                                code=retry_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                                message=retry_facade()._SECURITY_WORK_RETRY_MESSAGE,
                                request_id=state.request_id,
                                action="retry_security_work_authorized",
                                account_id=state.account.id,
                            )
                        )
                        await release_tracked_stream_lease(state, state.current_lease)
                        state.current_lease = None
                        state.excluded_account_ids.add(state.account.id)
                        state.require_security_work_authorized = True
                        state.last_security_work_retry_error = exc
                        continue
                    await state.proxy._handle_stream_error(state.account, exc.error, exc.code)
                    state.last_retryable_stream_error = exc
                    if exc.exclude_account:
                        await release_tracked_stream_lease(state, state.current_lease)
                        state.current_lease = None
                        state.excluded_account_ids.add(state.account.id)
                    continue
                except _TerminalStreamError as exc:
                    if retry_facade()._should_penalize_stream_error(exc.code):
                        await state.proxy._handle_stream_error(state.account, exc.error, exc.code)
                    return
                except ProxyResponseError as exc:
                    async for event in recover_stream_response_error(state, exc):
                        yield event
                    if state.stop:
                        return
                    continue
                except RefreshError as exc:
                    if exc.is_permanent:
                        await state.proxy._load_balancer.mark_permanent_failure(state.account, exc.code)
                    continue
                except Exception:
                    retry_facade().logger.warning(
                        "Proxy streaming failed without retry account_id=%s request_id=%s",
                        state.account_id_value,
                        state.request_id,
                        exc_info=True,
                    )
                    state.event = response_failed_event(
                        "upstream_error",
                        "Proxy streaming failed",
                        response_id=state.request_id,
                    )
                    yield format_sse_event(state.event)
                    return

            if state.propagate_http_errors and state.last_transient_exc is not None:
                raise state.last_transient_exc
            if state.last_retryable_stream_error is not None:
                state.retries_exhausted_msg = str(
                    state.last_retryable_stream_error.error.get("message") or "Upstream error"
                )
                state.event = response_failed_event(
                    state.last_retryable_stream_error.code,
                    state.retries_exhausted_msg,
                    response_id=state.request_id,
                )
                yield format_sse_event(state.event)
                if not state.any_attempt_logged:
                    await state.proxy._write_request_log(
                        identity=state.request_identity,
                        account_id=None,
                        api_key=state.api_key,
                        request_id=state.request_id,
                        model=state.payload.model,
                        latency_ms=int((time.monotonic() - state.start) * 1000),
                        status="error",
                        error_code=state.last_retryable_stream_error.code,
                        error_message=state.retries_exhausted_msg,
                        reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                        transport=state.request_transport,
                        service_tier=state.payload.service_tier,
                        requested_service_tier=state.payload.service_tier,
                        useragent=state.useragent,
                        useragent_group=state.useragent_group,
                    )
                return
            state.retries_exhausted_msg = "No available accounts after retries"
            retry_facade().logger.warning(
                "Proxy streaming exhausted accounts request_id=%s model=%s transport=%s attempts=%s "
                "excluded_count=%s preferred_account_id=%s api_key_present=%s",
                state.request_id,
                state.payload.model,
                state.request_transport,
                state.attempt,
                len(state.excluded_account_ids),
                state.preferred_account_id,
                state.api_key is not None,
            )
            state.event = response_failed_event(
                "no_accounts",
                state.retries_exhausted_msg,
                response_id=state.request_id,
            )
            yield format_sse_event(state.event)
            if not state.any_attempt_logged:
                await state.proxy._write_request_log(
                    identity=state.request_identity,
                    account_id=None,
                    api_key=state.api_key,
                    request_id=state.request_id,
                    model=state.payload.model,
                    latency_ms=int((time.monotonic() - state.start) * 1000),
                    status="error",
                    error_code="no_accounts",
                    error_message=state.retries_exhausted_msg,
                    reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                    transport=state.request_transport,
                    service_tier=state.payload.service_tier,
                    requested_service_tier=state.payload.service_tier,
                    useragent=state.useragent,
                    useragent_group=state.useragent_group,
                )
        finally:
            for account_lease in state.account_leases:
                await state.proxy._load_balancer.release_account_lease(account_lease)
            if not state.settled and state.api_key is not None and state.api_key_reservation is not None:
                state.release_coro = state.proxy._release_unsettled_stream_api_key_usage(
                    api_key=state.api_key,
                    api_key_reservation=state.api_key_reservation,
                    request_id=state.request_id,
                )
                state.current_task = asyncio.current_task()
                if state.current_task is not None and state.current_task.cancelling():
                    state.proxy._schedule_cancel_safe_cleanup(
                        state.release_coro,
                        action="release_stream_api_key_reservation",
                        request_id=state.request_id,
                    )
                else:
                    await state.release_coro
