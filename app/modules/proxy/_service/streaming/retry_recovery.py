from __future__ import annotations

import asyncio
from typing import AsyncIterator

import aiohttp

from app.core.auth.refresh import RefreshError
from app.core.balancer import failover_decision
from app.core.clients.proxy import ProxyResponseError, pop_stream_timeout_overrides
from app.core.errors import response_failed_event
from app.core.utils.sse import format_sse_event
from app.modules.proxy._service.streaming.retry_state import RetryState, release_tracked_stream_lease, retry_facade


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
