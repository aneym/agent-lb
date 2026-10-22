from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.core.balancer import failover_decision
from app.core.clients.proxy import ProxyResponseError, pop_stream_timeout_overrides
from app.core.errors import response_failed_event
from app.core.utils.retry import backoff_seconds
from app.core.utils.sse import format_sse_event
from app.modules.proxy._service.streaming.retry_state import (
    PROTOCOL_ONLY_STREAM_EVENT_TYPES,
    PROTOCOL_ONLY_TERMINAL_ERROR_TYPES,
    RetryState,
    release_tracked_stream_lease,
    retry_facade,
)


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
