from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, cast

import aiohttp

from app.core.auth.refresh import RefreshError
from app.core.balancer import failover_decision
from app.core.clients.proxy import ProxyResponseError, pop_stream_timeout_overrides
from app.core.errors import openai_error, response_failed_event
from app.core.identity import get_request_identity
from app.core.openai.requests import ResponsesRequest
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.request_id import ensure_request_id
from app.core.utils.retry import backoff_seconds
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import StickySessionKind
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy._service.observability import (
    _maybe_log_proxy_request_shape,
    _record_continuity_fail_closed,
)
from app.modules.proxy._service.streaming.protocol import _StreamingServiceProtocol
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
from app.modules.proxy.load_balancer import AccountLease
from app.modules.proxy.selection_diagnostics import selection_error_extras

REQUEST_TRANSPORT_HTTP = "http"
PROTOCOL_ONLY_STREAM_EVENT_TYPES = frozenset({"response.created", "response.in_progress", "response.queued"})
PROTOCOL_ONLY_TERMINAL_ERROR_TYPES = frozenset({"response.failed", "error"})


def retry_facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


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


@dataclass
class RetryState:
    stop: bool = False
    RetryableStreamError: Any = None
    StreamSettlement: Any = None
    TransientStreamError: Any = None
    WebSocketUpstreamControl: Any = None
    apply_error_metadata: Any = None
    normalize_error_code: Any = None
    owner_lookup_session_id_from_headers: Any = None
    parse_openai_error: Any = None
    record_continuity_fail_closed: Any = None
    stream_settlement_error_payload: Any = None
    upstream_error_from_openai: Any = None
    protocol_only_stream_error: Any = None
    protocol_only_stream_event_type: Any = None
    account: Any = None
    account_id_value: Any = None
    account_leases: Any = None
    action: Any = None
    affinity: Any = None
    allow_retry_flag: Any = None
    any_attempt_logged: Any = None
    api_key: Any = None
    api_key_reservation: Any = None
    attempt: Any = None
    base_settings: Any = None
    buffered_line: Any = None
    candidates_remaining: Any = None
    classified: Any = None
    code: Any = None
    codex_session_affinity: Any = None
    current_lease: Any = None
    current_task: Any = None
    deadline: Any = None
    delay: Any = None
    downstream_visible: Any = None
    effective_attempt_timeout: Any = None
    error: Any = None
    error_code: Any = None
    error_message: Any = None
    error_param: Any = None
    error_payload: Any = None
    error_type: Any = None
    estimated_lease_tokens: Any = None
    event: Any = None
    event_type: Any = None
    excluded_account_ids: Any = None
    failed_response_id: Any = None
    file_preferred_account_id: Any = None
    file_required_preferred_account: Any = None
    had_prompt_cache_key: Any = None
    headers: Any = None
    last_retryable_stream_error: Any = None
    last_security_work_retry_error: Any = None
    last_transient_exc: Any = None
    line: Any = None
    max_attempts: Any = None
    message: Any = None
    no_accounts_msg: Any = None
    openai_cache_affinity: Any = None
    payload: Any = None
    prefer_earlier_reset: Any = None
    preferred_account_id: Any = None
    previous_response_lookup_session_id: Any = None
    propagate_http_errors: Any = None
    protocol_only_buffer: Any = None
    protocol_only_buffer_committed: Any = None
    protocol_only_terminal_error_code: Any = None
    protocol_only_terminal_error_message: Any = None
    proxy: Any = None
    release_coro: Any = None
    remaining_budget: Any = None
    request_id: Any = None
    request_identity: Any = None
    request_transport: Any = None
    require_preferred_account: Any = None
    require_security_work_authorized: Any = None
    resolved_file_account_id: Any = None
    retries_exhausted_msg: Any = None
    retry_protocol_only_failures: Any = None
    rewritten_file_account_id: Any = None
    routing_strategy: Any = None
    selection: Any = None
    selection_inputs: Any = None
    settings: Any = None
    settled: Any = None
    settlement: Any = None
    start: Any = None
    sticky_key_source: Any = None
    stream_timeout_tokens: Any = None
    suppress_text_done_events: Any = None
    tool_call_dedupe: Any = None
    transient_retries: Any = None
    upstream_stream_transport: Any = None
    upstream_stream_transport_override: Any = None
    useragent: Any = None
    useragent_group: Any = None


async def release_tracked_stream_lease(state: RetryState, lease: AccountLease | None) -> None:
    if lease is None:
        return
    try:
        state.account_leases.remove(lease)
    except ValueError:
        pass
    await state.proxy._load_balancer.release_account_lease(lease)


async def resolve_retry_affinity(state: RetryState) -> AsyncIterator[str]:
    if state.payload.previous_response_id is not None:
        state.previous_response_lookup_session_id = state.owner_lookup_session_id_from_headers(state.headers)
        state.preferred_account_id = await state.proxy._resolve_websocket_previous_response_owner(
            previous_response_id=state.payload.previous_response_id,
            api_key=state.api_key,
            session_id=state.previous_response_lookup_session_id,
            surface="http_stream",
        )
        state.require_preferred_account = state.preferred_account_id is not None

        if state.preferred_account_id is None:
            state.selection_inputs = await state.proxy._load_balancer._load_selection_inputs(
                model=state.payload.model,
                additional_limit_name=None,
                account_ids=None,
            )
            if len(state.selection_inputs.accounts) != 1:
                state.message = "Previous response owner account is unavailable; retry later."
                state.record_continuity_fail_closed(
                    surface="http_stream",
                    reason="owner_account_unavailable",
                    previous_response_id=state.payload.previous_response_id,
                    session_id=state.previous_response_lookup_session_id,
                    upstream_error_code="owner_lookup_miss",
                )
                state.event = response_failed_event(
                    "previous_response_owner_unavailable",
                    state.message,
                    response_id=state.request_id,
                )
                yield format_sse_event(state.event)
                await state.proxy._write_request_log(
                    identity=state.request_identity,
                    account_id=None,
                    api_key=state.api_key,
                    request_id=state.request_id,
                    model=state.payload.model,
                    latency_ms=int((time.monotonic() - state.start) * 1000),
                    status="error",
                    error_code="previous_response_owner_unavailable",
                    error_message=state.message,
                    reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                    transport=state.request_transport,
                    service_tier=state.payload.service_tier,
                    requested_service_tier=state.payload.service_tier,
                    useragent=state.useragent,
                    useragent_group=state.useragent_group,
                )
                state.stop = True
                return
    state.file_required_preferred_account = False
    if state.preferred_account_id is None:
        if state.rewritten_file_account_id is not None:
            state.preferred_account_id = state.rewritten_file_account_id
            state.file_required_preferred_account = True
    if state.preferred_account_id is None:
        state.resolved_file_account_id = await state.proxy._resolve_file_account_for_responses(
            state.payload, state.headers
        )
        if state.resolved_file_account_id is not None:
            state.file_preferred_account_id = state.resolved_file_account_id
            state.preferred_account_id = state.resolved_file_account_id
            state.file_required_preferred_account = True


async def select_retry_account(state: RetryState) -> AsyncIterator[str]:
    state.remaining_budget = retry_facade()._remaining_budget_seconds(state.deadline)
    if state.remaining_budget <= 0:
        retry_facade().logger.warning(
            "Proxy request budget exhausted before retry request_id=%s attempt=%s",
            state.request_id,
            state.attempt + 1,
        )
        await state.proxy._write_stream_preflight_error(
            account_id=None,
            api_key=state.api_key,
            request_id=state.request_id,
            model=state.payload.model,
            start=state.start,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
            reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
            service_tier=state.payload.service_tier,
            transport=state.request_transport,
            useragent=state.useragent,
            useragent_group=state.useragent_group,
        )
        yield format_sse_event(retry_facade()._proxy_request_timeout_event(state.request_id))
        state.stop = True
        return
    while True:
        try:
            state.selection = await state.proxy._select_account_with_budget_compatible(
                state.deadline,
                request_id=state.request_id,
                kind="stream",
                api_key=state.api_key,
                sticky_key=state.affinity.key,
                sticky_kind=state.affinity.kind,
                reallocate_sticky=state.affinity.reallocate_sticky,
                sticky_max_age_seconds=state.affinity.max_age_seconds,
                prefer_earlier_reset_accounts=state.prefer_earlier_reset,
                prefer_earlier_reset_window=retry_facade()._prefer_earlier_reset_window(state.settings),
                routing_strategy=state.routing_strategy,
                model=state.payload.model,
                exclude_account_ids=state.excluded_account_ids,
                preferred_account_id=state.preferred_account_id,
                require_security_work_authorized=state.require_security_work_authorized,
                lease_kind="stream",
                estimated_lease_tokens=state.estimated_lease_tokens,
                fallback_on_preferred_account_unavailable=not state.file_required_preferred_account,
            )
        except ProxyResponseError as exc:
            state.error = state.parse_openai_error(exc.payload)
            state.error_code = state.normalize_error_code(
                state.error.code if state.error else None, state.error.type if state.error else None
            )
            state.error_message = state.error.message if state.error else None
            if retry_facade()._is_proxy_budget_exhausted_error(exc):
                await state.proxy._write_stream_preflight_error(
                    account_id=None,
                    api_key=state.api_key,
                    request_id=state.request_id,
                    model=state.payload.model,
                    start=state.start,
                    error_code="upstream_request_timeout",
                    error_message="Proxy request budget exhausted",
                    reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                    service_tier=state.payload.service_tier,
                    transport=state.request_transport,
                    useragent=state.useragent,
                    useragent_group=state.useragent_group,
                )
                yield format_sse_event(retry_facade()._proxy_request_timeout_event(state.request_id))
                state.stop = True
                return
            state.event = response_failed_event(
                state.error_code,
                state.error_message or "Upstream unavailable",
                error_type=(state.error.type or "server_error") if state.error else "server_error",
                response_id=state.request_id,
            )
            state.apply_error_metadata(state.event["response"]["error"], state.error)
            yield format_sse_event(state.event)
            state.stop = True
            return
        state.account = state.selection.account
        state.current_lease = state.selection.lease
        if state.selection.lease is not None:
            state.account_leases.append(state.selection.lease)
        if (
            not state.account
            and state.require_security_work_authorized
            and state.selection.error_code == retry_facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE
        ):
            retry_facade().logger.info(
                "No security-work-authorized account available for stream retry; "
                "continuing normal account failover request_id=%s",
                state.request_id,
            )
            yield format_sse_event(
                retry_facade()._security_work_advisory_event(
                    code=retry_facade()._NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE,
                    message=retry_facade()._SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE,
                    request_id=state.request_id,
                    action="continue_normal_selection",
                )
            )
            state.require_security_work_authorized = False
            continue
        break
    if not state.account:
        if retry_facade()._is_local_account_cap_code(state.selection.error_code):
            raise ProxyResponseError(
                429,
                openai_error(
                    state.selection.error_code or "account_stream_cap",
                    state.selection.error_message or "Account stream capacity is exhausted",
                    error_type="rate_limit_error",
                ),
            )
        if state.require_preferred_account and state.preferred_account_id is not None:
            state.message = "Previous response owner account is unavailable; retry later."
            state.record_continuity_fail_closed(
                surface="http_stream",
                reason="owner_account_unavailable",
                previous_response_id=state.payload.previous_response_id,
                session_id=state.headers.get("x-codex-turn-state") or state.headers.get("session_id"),
                upstream_error_code="no_accounts",
            )
            state.event = response_failed_event(
                "previous_response_owner_unavailable",
                state.message,
                response_id=state.request_id,
            )
            yield format_sse_event(state.event)
            await state.proxy._write_request_log(
                identity=state.request_identity,
                account_id=state.preferred_account_id,
                api_key=state.api_key,
                request_id=state.request_id,
                model=state.payload.model,
                latency_ms=int((time.monotonic() - state.start) * 1000),
                status="error",
                error_code="previous_response_owner_unavailable",
                error_message=state.message,
                reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                transport=state.request_transport,
                service_tier=state.payload.service_tier,
                requested_service_tier=state.payload.service_tier,
                useragent=state.useragent,
                useragent_group=state.useragent_group,
            )
            state.stop = True
            return

        if state.propagate_http_errors and state.last_transient_exc is not None:
            raise state.last_transient_exc
        if state.last_retryable_stream_error is not None:
            state.error_message = str(state.last_retryable_stream_error.error.get("message") or "Upstream error")
            state.event = response_failed_event(
                state.last_retryable_stream_error.code,
                state.error_message,
                response_id=state.request_id,
            )
            yield format_sse_event(state.event)
            await state.proxy._write_request_log(
                identity=state.request_identity,
                account_id=None,
                api_key=state.api_key,
                request_id=state.request_id,
                model=state.payload.model,
                latency_ms=int((time.monotonic() - state.start) * 1000),
                status="error",
                error_code=state.last_retryable_stream_error.code,
                error_message=state.error_message,
                reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                transport=state.request_transport,
                service_tier=state.payload.service_tier,
                requested_service_tier=state.payload.service_tier,
                useragent=state.useragent,
                useragent_group=state.useragent_group,
            )
            state.stop = True
            return
        if state.last_security_work_retry_error is not None:
            state.message = (
                state.last_security_work_retry_error.error.get("message") or "Security work authorization is required"
            )
            state.event = response_failed_event(
                state.last_security_work_retry_error.code,
                state.message,
                response_id=state.request_id,
            )
            yield format_sse_event(state.event)
            await state.proxy._write_request_log(
                identity=state.request_identity,
                account_id=None,
                api_key=state.api_key,
                request_id=state.request_id,
                model=state.payload.model,
                latency_ms=int((time.monotonic() - state.start) * 1000),
                status="error",
                error_code=state.last_security_work_retry_error.code,
                error_message=state.message,
                reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                transport=state.request_transport,
                service_tier=state.payload.service_tier,
                requested_service_tier=state.payload.service_tier,
                useragent=state.useragent,
                useragent_group=state.useragent_group,
            )
            state.stop = True
            return
        state.no_accounts_msg = state.selection.error_message or "No active accounts available"
        state.error_code = state.selection.error_code or "no_accounts"
        state.event = response_failed_event(
            state.error_code,
            state.no_accounts_msg,
            response_id=state.request_id,
        )
        state.event["response"]["error"].update(
            selection_error_extras(state.selection, requested_model=state.payload.model)
        )
        yield format_sse_event(state.event)
        await state.proxy._write_request_log(
            identity=state.request_identity,
            account_id=None,
            api_key=state.api_key,
            request_id=state.request_id,
            model=state.payload.model,
            latency_ms=int((time.monotonic() - state.start) * 1000),
            status="error",
            error_code=state.error_code,
            error_message=state.no_accounts_msg,
            reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
            transport=state.request_transport,
            service_tier=state.payload.service_tier,
            requested_service_tier=state.payload.service_tier,
            useragent=state.useragent,
            useragent_group=state.useragent_group,
        )
        state.stop = True
        return

    state.account_id_value = state.account.id
    if (
        state.require_preferred_account
        and state.preferred_account_id is not None
        and state.account.id != state.preferred_account_id
    ):
        state.message = "Previous response owner account is unavailable; retry later."
        state.record_continuity_fail_closed(
            surface="http_stream",
            reason="owner_account_unavailable",
            previous_response_id=state.payload.previous_response_id,
            session_id=state.headers.get("x-codex-turn-state") or state.headers.get("session_id"),
            upstream_error_code="upstream_unavailable",
        )
        state.event = response_failed_event(
            "previous_response_owner_unavailable",
            state.message,
            response_id=state.request_id,
        )
        yield format_sse_event(state.event)
        await state.proxy._write_request_log(
            identity=state.request_identity,
            account_id=state.preferred_account_id,
            api_key=state.api_key,
            request_id=state.request_id,
            model=state.payload.model,
            latency_ms=int((time.monotonic() - state.start) * 1000),
            status="error",
            error_code="previous_response_owner_unavailable",
            error_message=state.message,
            reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
            transport=state.request_transport,
            service_tier=state.payload.service_tier,
            requested_service_tier=state.payload.service_tier,
            useragent=state.useragent,
            useragent_group=state.useragent_group,
        )
        state.stop = True
        return


async def stream_account_attempts(state: RetryState) -> AsyncIterator[str]:
    state.allow_retry_flag = state.attempt < state.max_attempts - 1
    while True:
        state.stream_timeout_tokens = retry_facade()._push_stream_attempt_timeout_overrides(
            retry_facade()._remaining_budget_seconds(state.deadline),
        )
        try:
            state.settlement = state.StreamSettlement()
            state.protocol_only_buffer = []
            state.protocol_only_buffer_committed = not state.retry_protocol_only_failures
            state.protocol_only_terminal_error_code = None
            state.protocol_only_terminal_error_message = None
            async for state.line in state.proxy._stream_once(
                state.account,
                state.payload,
                state.headers,
                state.request_id,
                state.allow_retry_flag,
                request_started_at=state.start,
                allow_transient_retry=(
                    state.transient_retries < retry_facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES - 1
                    or state.allow_retry_flag
                ),
                api_key=state.api_key,
                api_key_reservation=state.api_key_reservation,
                settlement=state.settlement,
                suppress_text_done_events=state.suppress_text_done_events,
                upstream_stream_transport=state.upstream_stream_transport,
                request_transport=state.request_transport,
                useragent=state.useragent,
                useragent_group=state.useragent_group,
                preferred_account_id=state.preferred_account_id,
                tool_call_dedupe=state.tool_call_dedupe,
            ):
                if state.retry_protocol_only_failures and not state.protocol_only_buffer_committed:
                    state.protocol_only_buffer.append(state.line)
                    state.event_type = state.protocol_only_stream_event_type(state.line)
                    if state.event_type in PROTOCOL_ONLY_STREAM_EVENT_TYPES or state.event_type is None:
                        continue
                    if state.event_type in PROTOCOL_ONLY_TERMINAL_ERROR_TYPES:
                        (
                            state.protocol_only_terminal_error_code,
                            state.protocol_only_terminal_error_message,
                        ) = state.protocol_only_stream_error(state.line)
                        continue
                    state.protocol_only_buffer_committed = True
                    for state.buffered_line in state.protocol_only_buffer:
                        yield state.buffered_line
                    state.protocol_only_buffer.clear()
                    continue
                yield state.line
        except (state.TransientStreamError, ProxyResponseError) as tex:
            state.downstream_visible = state.settlement.downstream_visible
            if state.retry_protocol_only_failures and not state.protocol_only_buffer_committed:
                state.downstream_visible = False
            if state.downstream_visible:
                state.failed_response_id = state.settlement.response_id or state.request_id
                if isinstance(tex, ProxyResponseError):
                    state.error = state.parse_openai_error(tex.payload)
                    state.error_code = state.normalize_error_code(
                        state.error.code if state.error else None,
                        state.error.type if state.error else None,
                    )
                    state.error_message = state.error.message if state.error else "Upstream error"
                    state.error_type = state.error.type if state.error else None
                    state.error_param = state.error.param if state.error else None
                    state.event = response_failed_event(
                        state.error_code or "upstream_error",
                        state.error_message or "Upstream error",
                        error_type=state.error_type or "server_error",
                        response_id=state.failed_response_id,
                        error_param=state.error_param,
                    )
                    state.apply_error_metadata(state.event["response"]["error"], state.error)
                else:
                    state.error_code = tex.code
                    state.error_message = str(tex.error.get("message") or "Upstream error")
                    state.event = response_failed_event(
                        state.error_code or "upstream_error",
                        state.error_message,
                        response_id=state.failed_response_id,
                    )
                retry_facade().logger.warning(
                    "Surfacing mid-stream upstream failure without replay request_id=%s account_id=%s code=%s",
                    state.request_id,
                    state.account.id,
                    state.error_code,
                )
                yield format_sse_event(state.event)
                state.settlement.record_success = False
                state.settlement.error_code = state.error_code
                state.settlement.error_message = state.error_message
                if isinstance(tex, ProxyResponseError):
                    state.settlement.error = state.upstream_error_from_openai(state.error)
                else:
                    state.settlement.error = tex.error
                state.settlement.account_health_error = retry_facade()._should_penalize_stream_error(state.error_code)
                if state.settlement.account_health_error:
                    await state.proxy._handle_stream_error(
                        state.account,
                        state.stream_settlement_error_payload(state.settlement),
                        state.settlement.error_code or "upstream_error",
                    )
                state.settled = await state.proxy._settle_stream_api_key_usage(
                    state.api_key,
                    state.api_key_reservation,
                    state.settlement,
                    state.request_id,
                )
                state.stop = True
                return
            if isinstance(tex, ProxyResponseError) and tex.status_code != 500:
                state.error = state.parse_openai_error(tex.payload)
                state.code = state.normalize_error_code(
                    state.error.code if state.error else None,
                    state.error.type if state.error else None,
                )
                state.error_message = state.error.message if state.error else None
                if retry_facade()._is_security_work_authorization_required_error(state.code, state.error_message):
                    if (
                        state.account.security_work_authorized
                        or state.account.id == state.file_preferred_account_id
                        or state.require_preferred_account
                        or state.attempt >= state.max_attempts - 1
                    ):
                        raise
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
                    state.last_security_work_retry_error = state.RetryableStreamError(
                        retry_facade()._SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
                        state.upstream_error_from_openai(state.error),
                    )
                    state.last_transient_exc = tex
                    break
                if state.code == "account_response_create_cap":
                    state.last_transient_exc = tex
                    await release_tracked_stream_lease(state, state.current_lease)
                    state.current_lease = None
                    state.excluded_account_ids.add(state.account.id)
                    break
                if retry_facade()._is_account_neutral_error_code(state.code):
                    raise
                state.classified = await state.proxy._handle_stream_error(
                    state.account,
                    state.upstream_error_from_openai(state.error),
                    state.code,
                    http_status=tex.status_code,
                )
                if getattr(state.base_settings, "deterministic_failover_enabled", True):
                    state.action = failover_decision(
                        failure_class=state.classified["failure_class"],
                        downstream_visible=state.downstream_visible,
                        candidates_remaining=state.max_attempts - state.attempt - 1,
                    )
                else:
                    state.action = "surface"
                retry_facade().logger.info(
                    "Failover decision request_id=%s transport=stream account_id=%s "
                    "attempt=%d failure_class=%s action=%s",
                    state.request_id,
                    state.account.id,
                    state.attempt + 1,
                    state.classified["failure_class"],
                    state.action,
                )
                if state.action == "failover_next":
                    state.last_transient_exc = tex
                    await release_tracked_stream_lease(state, state.current_lease)
                    state.current_lease = None
                    state.excluded_account_ids.add(state.account.id)
                    break
                raise
            state.transient_retries += 1
            state.error_code = tex.code if isinstance(tex, state.TransientStreamError) else "server_error"
            state.error_payload = (
                tex.error
                if isinstance(tex, state.TransientStreamError)
                else state.upstream_error_from_openai(state.parse_openai_error(tex.payload))
            )
            if (
                state.transient_retries < retry_facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES
                and retry_facade()._remaining_budget_seconds(state.deadline) > 0
                and not state.downstream_visible
            ):
                state.delay = backoff_seconds(state.transient_retries)
                retry_facade().logger.info(
                    "Transient stream error, retrying same account "
                    "request_id=%s account_id=%s retry=%s/%s delay=%.2fs code=%s",
                    state.request_id,
                    state.account.id,
                    state.transient_retries,
                    retry_facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES,
                    state.delay,
                    state.error_code,
                )
                await asyncio.sleep(state.delay)
                continue

            retry_facade().logger.warning(
                "Transient retries exhausted for account request_id=%s account_id=%s retries=%s code=%s",
                state.request_id,
                state.account.id,
                state.transient_retries,
                state.error_code,
            )
            await state.proxy._handle_stream_error(state.account, state.error_payload, state.error_code)

            await state.proxy._load_balancer.record_errors(state.account, state.transient_retries - 1)

            if isinstance(tex, ProxyResponseError):
                state.last_transient_exc = tex
            await release_tracked_stream_lease(state, state.current_lease)
            state.current_lease = None
            state.excluded_account_ids.add(state.account.id)
            break
        finally:
            pop_stream_timeout_overrides(state.stream_timeout_tokens)
        if (
            state.retry_protocol_only_failures
            and not state.protocol_only_buffer_committed
            and state.protocol_only_buffer
        ):
            state.error_code = state.protocol_only_terminal_error_code or state.settlement.error_code
            state.error_message = state.protocol_only_terminal_error_message or state.settlement.error_message
            if retry_facade()._should_retry_transient_stream_error(state.error_code, state.error_message):
                state.transient_retries += 1
                state.error_payload = state.settlement.error or {"message": state.error_message or "Upstream error"}
                if (
                    state.transient_retries < retry_facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES
                    and retry_facade()._remaining_budget_seconds(state.deadline) > 0
                ):
                    state.delay = backoff_seconds(state.transient_retries)
                    retry_facade().logger.info(
                        "Protocol-only stream failure, retrying before public output "
                        "request_id=%s account_id=%s retry=%s/%s delay=%.2fs code=%s",
                        state.request_id,
                        state.account.id,
                        state.transient_retries,
                        retry_facade()._MAX_TRANSIENT_SAME_ACCOUNT_RETRIES,
                        state.delay,
                        state.error_code,
                    )
                    await asyncio.sleep(state.delay)
                    continue
                retry_facade().logger.warning(
                    "Protocol-only stream retries exhausted for account request_id=%s account_id=%s retries=%s code=%s",
                    state.request_id,
                    state.account.id,
                    state.transient_retries,
                    state.error_code,
                )
                await state.proxy._handle_stream_error(
                    state.account,
                    state.error_payload,
                    state.error_code or "upstream_error",
                )
                await state.proxy._load_balancer.record_errors(state.account, state.transient_retries - 1)
                await release_tracked_stream_lease(state, state.current_lease)
                state.current_lease = None
                state.excluded_account_ids.add(state.account.id)
                break
            state.protocol_only_buffer_committed = True
            for state.buffered_line in state.protocol_only_buffer:
                yield state.buffered_line
            state.protocol_only_buffer.clear()
        if state.settlement.account_health_error:
            await state.proxy._handle_stream_error(
                state.account,
                state.stream_settlement_error_payload(state.settlement),
                state.settlement.error_code or "upstream_error",
            )
        elif state.settlement.record_success:
            await state.proxy._load_balancer.record_success(state.account)
        state.settled = await state.proxy._settle_stream_api_key_usage(
            state.api_key,
            state.api_key_reservation,
            state.settlement,
            state.request_id,
        )
        state.stop = True
        return


async def prepare_stream_attempt(state: RetryState) -> AsyncIterator[str]:
    state.remaining_budget = retry_facade()._remaining_budget_seconds(state.deadline)
    if state.remaining_budget <= 0:
        retry_facade().logger.warning(
            "Proxy request budget exhausted before freshness check request_id=%s attempt=%s account_id=%s",
            state.request_id,
            state.attempt + 1,
            state.account.id,
        )
        await state.proxy._write_stream_preflight_error(
            account_id=state.account.id,
            api_key=state.api_key,
            request_id=state.request_id,
            model=state.payload.model,
            start=state.start,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
            reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
            service_tier=state.payload.service_tier,
            transport=state.request_transport,
            useragent=state.useragent,
            useragent_group=state.useragent_group,
        )
        yield format_sse_event(retry_facade()._proxy_request_timeout_event(state.request_id))
        state.stop = True
        return
    try:
        state.account = await state.proxy._ensure_fresh_with_budget(
            state.account, timeout_seconds=state.remaining_budget
        )
    except UpstreamProxyRouteError as exc:
        state.message = f"Upstream proxy route unavailable: {exc.reason}"
        await state.proxy._write_stream_preflight_error(
            account_id=state.account.id,
            api_key=state.api_key,
            request_id=state.request_id,
            model=state.payload.model,
            start=state.start,
            error_code="upstream_proxy_unavailable",
            error_message=state.message,
            reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
            service_tier=state.payload.service_tier,
            transport=state.request_transport,
            upstream_proxy_fail_closed_reason=exc.reason,
            useragent=state.useragent,
            useragent_group=state.useragent_group,
        )
        state.event = response_failed_event(
            "upstream_proxy_unavailable",
            state.message,
            response_id=state.request_id,
        )
        yield format_sse_event(state.event)
        state.stop = True
        return
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        retry_facade().logger.warning(
            "Stream refresh/connect failed request_id=%s attempt=%s account_id=%s",
            state.request_id,
            state.attempt + 1,
            state.account.id,
            exc_info=True,
        )
        state.message = str(exc) or "Request to upstream timed out"
        if (
            not state.require_preferred_account
            and state.preferred_account_id is None
            and retry_facade()._should_retry_transient_stream_error("upstream_unavailable", state.message)
            and state.attempt + 1 < state.max_attempts
        ):
            await state.proxy._handle_stream_error(
                state.account,
                {"message": state.message},
                "upstream_unavailable",
            )
            state.last_retryable_stream_error = state.RetryableStreamError(
                "upstream_unavailable",
                {"message": state.message},
                exclude_account=True,
            )
            state.excluded_account_ids.add(state.account.id)
            return
        await state.proxy._write_stream_preflight_error(
            account_id=state.account.id,
            api_key=state.api_key,
            request_id=state.request_id,
            model=state.payload.model,
            start=state.start,
            error_code="upstream_unavailable",
            error_message=state.message,
            reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
            service_tier=state.payload.service_tier,
            transport=state.request_transport,
            useragent=state.useragent,
            useragent_group=state.useragent_group,
        )
        state.event = response_failed_event(
            "upstream_unavailable",
            state.message,
            response_id=state.request_id,
        )
        yield format_sse_event(state.event)
        state.stop = True
        return
    state.any_attempt_logged = True
    state.settlement = state.StreamSettlement()
    state.tool_call_dedupe = state.WebSocketUpstreamControl()
    state.effective_attempt_timeout = retry_facade()._remaining_budget_seconds(state.deadline)
    if state.effective_attempt_timeout <= 0:
        retry_facade().logger.warning(
            "Proxy request budget exhausted before stream attempt request_id=%s attempt=%s account_id=%s",
            state.request_id,
            state.attempt + 1,
            state.account.id,
        )
        await state.proxy._write_stream_preflight_error(
            account_id=state.account.id,
            api_key=state.api_key,
            request_id=state.request_id,
            model=state.payload.model,
            start=state.start,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
            reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
            service_tier=state.payload.service_tier,
            transport=state.request_transport,
            useragent=state.useragent,
            useragent_group=state.useragent_group,
        )
        yield format_sse_event(retry_facade()._proxy_request_timeout_event(state.request_id))
        state.stop = True
        return
    state.transient_retries = 0
    async for event in stream_account_attempts(state):
        yield event
    if state.stop:
        return


async def recover_stream_response_error(state: RetryState, exc: ProxyResponseError) -> AsyncIterator[str]:
    if exc.status_code == 401:
        state.remaining_budget = retry_facade()._remaining_budget_seconds(state.deadline)
        if state.remaining_budget <= 0:
            retry_facade().logger.warning(
                "Proxy request budget exhausted before forced refresh retry request_id=%s attempt=%s account_id=%s",
                state.request_id,
                state.attempt + 1,
                state.account.id,
            )
            await state.proxy._write_stream_preflight_error(
                account_id=state.account.id,
                api_key=state.api_key,
                request_id=state.request_id,
                model=state.payload.model,
                start=state.start,
                error_code="upstream_request_timeout",
                error_message="Proxy request budget exhausted",
                reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                service_tier=state.payload.service_tier,
                transport=state.request_transport,
                useragent=state.useragent,
                useragent_group=state.useragent_group,
            )
            yield format_sse_event(retry_facade()._proxy_request_timeout_event(state.request_id))
            state.stop = True
            return
        try:
            state.account = await state.proxy._ensure_fresh_with_budget(
                state.account,
                force=True,
                timeout_seconds=state.remaining_budget,
            )
        except RefreshError as refresh_exc:
            if refresh_exc.is_permanent:
                await state.proxy._load_balancer.mark_permanent_failure(state.account, refresh_exc.code)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            retry_facade().logger.warning(
                "Stream forced refresh/connect failed request_id=%s attempt=%s account_id=%s",
                state.request_id,
                state.attempt + 1,
                state.account.id,
                exc_info=True,
            )
            state.message = str(exc) or "Request to upstream timed out"
            if (
                not state.require_preferred_account
                and state.preferred_account_id is None
                and retry_facade()._should_retry_transient_stream_error("upstream_unavailable", state.message)
                and state.attempt + 1 < state.max_attempts
            ):
                await state.proxy._handle_stream_error(
                    state.account,
                    {"message": state.message},
                    "upstream_unavailable",
                )
                state.last_retryable_stream_error = state.RetryableStreamError(
                    "upstream_unavailable",
                    {"message": state.message},
                    exclude_account=True,
                )
                state.excluded_account_ids.add(state.account.id)
                return
            await state.proxy._write_stream_preflight_error(
                account_id=state.account.id,
                api_key=state.api_key,
                request_id=state.request_id,
                model=state.payload.model,
                start=state.start,
                error_code="upstream_unavailable",
                error_message=state.message,
                reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                service_tier=state.payload.service_tier,
                transport=state.request_transport,
                useragent=state.useragent,
                useragent_group=state.useragent_group,
            )
            state.event = response_failed_event(
                "upstream_unavailable",
                state.message,
                response_id=state.request_id,
            )
            yield format_sse_event(state.event)
            state.stop = True
            return
        state.settlement = state.StreamSettlement()
        state.effective_attempt_timeout = retry_facade()._remaining_budget_seconds(state.deadline)
        if state.effective_attempt_timeout <= 0:
            retry_facade().logger.warning(
                "Proxy request budget exhausted before post-refresh stream attempt "
                "request_id=%s attempt=%s account_id=%s",
                state.request_id,
                state.attempt + 1,
                state.account.id,
            )
            await state.proxy._write_stream_preflight_error(
                account_id=state.account.id,
                api_key=state.api_key,
                request_id=state.request_id,
                model=state.payload.model,
                start=state.start,
                error_code="upstream_request_timeout",
                error_message="Proxy request budget exhausted",
                reasoning_effort=state.payload.reasoning.effort if state.payload.reasoning else None,
                service_tier=state.payload.service_tier,
                transport=state.request_transport,
                useragent=state.useragent,
                useragent_group=state.useragent_group,
            )
            yield format_sse_event(retry_facade()._proxy_request_timeout_event(state.request_id))
            state.stop = True
            return
        state.stream_timeout_tokens = retry_facade()._push_stream_attempt_timeout_overrides(
            state.effective_attempt_timeout
        )
        try:
            async for state.line in state.proxy._stream_once(
                state.account,
                state.payload,
                state.headers,
                state.request_id,
                False,
                request_started_at=state.start,
                api_key=state.api_key,
                api_key_reservation=state.api_key_reservation,
                settlement=state.settlement,
                suppress_text_done_events=state.suppress_text_done_events,
                upstream_stream_transport=state.upstream_stream_transport,
                request_transport=state.request_transport,
                useragent=state.useragent,
                useragent_group=state.useragent_group,
                tool_call_dedupe=state.tool_call_dedupe,
            ):
                yield state.line
        except ProxyResponseError as retry_exc:
            if state.settlement.downstream_visible:
                state.failed_response_id = state.settlement.response_id or state.request_id
                state.error = state.parse_openai_error(retry_exc.payload)
                state.error_code = state.normalize_error_code(
                    state.error.code if state.error else None,
                    state.error.type if state.error else None,
                )
                state.error_message = state.error.message if state.error else "Upstream error"
                state.event = response_failed_event(
                    state.error_code or "upstream_error",
                    state.error_message or "Upstream error",
                    error_type=(state.error.type if state.error else None) or "server_error",
                    response_id=state.failed_response_id,
                    error_param=state.error.param if state.error else None,
                )
                state.apply_error_metadata(state.event["response"]["error"], state.error)
                retry_facade().logger.warning(
                    "Surfacing post-refresh stream failure without replay request_id=%s account_id=%s code=%s",
                    state.request_id,
                    state.account.id,
                    state.error_code,
                )
                yield format_sse_event(state.event)
                state.settlement.record_success = False
                state.settlement.error_code = state.error_code
                state.settlement.error_message = state.error_message
                state.settlement.error = state.upstream_error_from_openai(state.error)
                state.settlement.account_health_error = retry_facade()._should_penalize_stream_error(state.error_code)
                if state.settlement.account_health_error:
                    await state.proxy._handle_stream_error(
                        state.account,
                        state.stream_settlement_error_payload(state.settlement),
                        state.settlement.error_code or "upstream_error",
                        http_status=retry_exc.status_code,
                    )
                state.settled = await state.proxy._settle_stream_api_key_usage(
                    state.api_key,
                    state.api_key_reservation,
                    state.settlement,
                    state.request_id,
                )
                state.stop = True
                return
            state.error = state.parse_openai_error(retry_exc.payload)
            state.error_code = state.normalize_error_code(
                state.error.code if state.error else None,
                state.error.type if state.error else None,
            )
            if state.error_code == "account_response_create_cap":
                state.last_transient_exc = retry_exc
                await release_tracked_stream_lease(state, state.current_lease)
                state.current_lease = None
                state.excluded_account_ids.add(state.account.id)
                return
            if retry_facade()._is_account_neutral_error_code(state.error_code):
                raise
            state.classified = await state.proxy._handle_stream_error(
                state.account,
                state.upstream_error_from_openai(state.error),
                state.error_code,
                http_status=retry_exc.status_code,
            )
            state.candidates_remaining = state.max_attempts - state.attempt - 1
            if retry_exc.status_code == 401 and state.candidates_remaining > 0:
                state.action = "failover_next"
            elif getattr(state.base_settings, "deterministic_failover_enabled", True):
                state.action = failover_decision(
                    failure_class=state.classified["failure_class"],
                    downstream_visible=False,
                    candidates_remaining=state.candidates_remaining,
                )
            else:
                state.action = "surface"
            retry_facade().logger.info(
                "Failover decision request_id=%s transport=stream account_id=%s "
                "attempt=%d phase=post_refresh failure_class=%s action=%s",
                state.request_id,
                state.account.id,
                state.attempt + 1,
                state.classified["failure_class"],
                state.action,
            )
            if state.action == "failover_next":
                state.last_transient_exc = retry_exc
                await release_tracked_stream_lease(state, state.current_lease)
                state.current_lease = None
                state.excluded_account_ids.add(state.account.id)
                return
            if state.propagate_http_errors:
                raise
            state.error_message = state.error.message if state.error else None
            state.event = response_failed_event(
                state.error_code or "upstream_error",
                state.error_message or "Upstream error",
                error_type=(state.error.type if state.error else None) or "server_error",
                response_id=state.request_id,
                error_param=state.error.param if state.error else None,
            )
            state.apply_error_metadata(state.event["response"]["error"], state.error)
            yield format_sse_event(state.event)
            state.stop = True
            return
        finally:
            pop_stream_timeout_overrides(state.stream_timeout_tokens)
        if state.settlement.account_health_error:
            await state.proxy._handle_stream_error(
                state.account,
                state.stream_settlement_error_payload(state.settlement),
                state.settlement.error_code or "upstream_error",
            )
        elif state.settlement.record_success:
            await state.proxy._load_balancer.record_success(state.account)
        state.settled = await state.proxy._settle_stream_api_key_usage(
            state.api_key,
            state.api_key_reservation,
            state.settlement,
            state.request_id,
        )
        state.stop = True
        return
    state.error = state.parse_openai_error(exc.payload)
    state.error_code = state.normalize_error_code(
        state.error.code if state.error else None, state.error.type if state.error else None
    )
    state.error_message = state.error.message if state.error else None
    state.error_type = state.error.type if state.error else None
    state.error_param = state.error.param if state.error else None
    if retry_facade()._is_security_work_authorization_required_error(state.error_code, state.error_message):
        if (
            not state.account.security_work_authorized
            and state.account.id != state.file_preferred_account_id
            and not state.require_preferred_account
            and state.attempt < state.max_attempts - 1
        ):
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
            return
    if retry_facade()._should_penalize_stream_error(state.error_code):
        await state.proxy._handle_stream_error(
            state.account,
            state.upstream_error_from_openai(state.error),
            state.error_code,
        )
    if state.propagate_http_errors:
        raise
    state.event = response_failed_event(
        state.error_code,
        state.error_message or "Upstream error",
        error_type=state.error_type or "server_error",
        response_id=state.request_id,
        error_param=state.error_param,
    )
    state.apply_error_metadata(state.event["response"]["error"], state.error)
    yield format_sse_event(state.event)
    state.stop = True
    return


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
