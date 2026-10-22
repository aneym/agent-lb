from __future__ import annotations

import time
from typing import AsyncIterator

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error, response_failed_event
from app.core.utils.sse import format_sse_event
from app.modules.proxy._service.streaming.retry_state import RetryState, retry_facade
from app.modules.proxy.selection_diagnostics import selection_error_extras


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
