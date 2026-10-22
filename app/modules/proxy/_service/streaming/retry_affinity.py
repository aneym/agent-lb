from __future__ import annotations

import time
from typing import AsyncIterator

from app.core.errors import response_failed_event
from app.core.utils.sse import format_sse_event
from app.modules.proxy._service.streaming.retry_state import RetryState


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
