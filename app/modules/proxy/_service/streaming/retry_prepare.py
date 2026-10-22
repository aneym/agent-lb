from __future__ import annotations

import asyncio
from typing import AsyncIterator

import aiohttp

from app.core.errors import response_failed_event
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.sse import format_sse_event
from app.modules.proxy._service.streaming.retry_attempt import stream_account_attempts
from app.modules.proxy._service.streaming.retry_state import RetryState, retry_facade


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
