from __future__ import annotations

import json
import sys
from collections.abc import Callable
from copy import deepcopy
from typing import Any, AsyncIterator, Literal, Mapping, cast

from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.core.errors import PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE, response_failed_event
from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
from app.core.types import JsonValue
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.modules.proxy._service.observability import _record_continuity_fail_closed as _record_continuity_fail_closed
from app.modules.proxy._service.support import _event_type_from_payload, _WebSocketRequestState
from app.modules.proxy._service.websocket.helpers import (
    _websocket_continuity_error_fields,
    _websocket_downstream_response_id,
)
from app.modules.proxy.helpers import _normalize_error_code


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


_REQUEST_TRANSPORT_HTTP = "http"


def _resolve_upstream_stream_transport(upstream_stream_transport: str) -> str | None:
    if upstream_stream_transport == "default":
        return None
    return upstream_stream_transport


def _should_penalize_stream_error(code: str | None) -> bool:
    if code is None:
        return False
    return code in _facade()._ACCOUNT_RECOVERY_RETRY_CODES or code in _facade()._TRANSIENT_RETRY_CODES


def _should_retry_transient_stream_error(code: str | None, message: str | None) -> bool:
    if code is None or code == "stream_idle_timeout":
        return False
    if code in _facade()._TRANSIENT_RETRY_CODES:
        return True
    if code != "upstream_unavailable" or not message:
        return False
    normalized_message = message.lower()
    if any(marker in normalized_message for marker in _facade()._UPSTREAM_UNAVAILABLE_NON_TRANSIENT_MESSAGE_MARKERS):
        return False
    return any(marker in normalized_message for marker in _facade()._UPSTREAM_UNAVAILABLE_TRANSIENT_MESSAGE_MARKERS)


def _refresh_upstream_proxy_fail_closed_reason(exc: RefreshError) -> str | None:
    if exc.code != "upstream_proxy_unavailable":
        return None
    reason = exc.upstream_proxy_fail_closed_reason
    if reason:
        return reason
    marker = "Upstream proxy route unavailable:"
    if exc.message.startswith(marker):
        parsed = exc.message.removeprefix(marker).strip()
        return parsed or "unavailable"
    return "unavailable"


def _classify_upstream_close(
    close_code: int | None,
    *,
    response_events_seen: int,
) -> Literal["transient", "rejected"]:
    if close_code == 1000 and response_events_seen == 0:
        return "rejected"
    return "transient"


def _should_infer_upstream_status_from_proxy_error(exc: ProxyResponseError, upstream_error_code: str | None) -> bool:
    if exc.failure_phase == "status":
        return True
    if exc.failure_phase is not None:
        return False
    return upstream_error_code not in _facade()._LOCAL_PROXY_ERROR_CODES


def _rewrite_previous_response_stream_error(
    *,
    previous_response_id: str | None,
    preferred_account_id: str | None,
    error_code: str | None,
    error_type: str | None,
    error_message: str | None,
    error_param: str | None,
) -> tuple[str, str, str | None] | None:
    if previous_response_id is None:
        return None
    if _facade()._is_previous_response_not_found_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        _record_continuity_fail_closed(
            surface="http_stream",
            reason="previous_response_not_found",
            previous_response_id=previous_response_id,
            upstream_error_code=error_code,
        )
        return (
            "stream_incomplete",
            PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
            None,
        )
    if _facade()._is_missing_tool_output_error(
        code=error_code,
        param=error_param,
        message=error_message,
    ):
        _record_continuity_fail_closed(
            surface="http_stream",
            reason="missing_tool_output",
            previous_response_id=previous_response_id,
            upstream_error_code=error_code,
        )
        return (
            "stream_incomplete",
            PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
            None,
        )
    normalized_code = _normalize_error_code(error_code, error_type)
    if preferred_account_id is not None and normalized_code in _facade()._ACCOUNT_RECOVERY_RETRY_CODES:
        _record_continuity_fail_closed(
            surface="http_stream",
            reason="owner_account_unavailable",
            previous_response_id=previous_response_id,
            upstream_error_code=normalized_code,
        )
        return (
            "previous_response_owner_unavailable",
            "Previous response owner account is unavailable; retry later.",
            normalized_code,
        )
    return None


def _build_rewritten_stream_response_failed_event(
    *,
    response_id: str,
    error_code: str,
    error_message: str,
) -> tuple[str, OpenAIEvent | None, dict[str, JsonValue] | None, str | None]:
    rewritten_event_payload = response_failed_event(
        error_code,
        error_message,
        error_type="server_error",
        response_id=response_id,
    )
    rewritten_event_block = format_sse_event(rewritten_event_payload)
    rewritten_payload = parse_sse_data_json(rewritten_event_block)
    rewritten_event = parse_sse_event(rewritten_event_block)
    rewritten_event_type = _event_type_from_payload(rewritten_event, rewritten_payload)
    return rewritten_event_block, rewritten_event, rewritten_payload, rewritten_event_type


def _build_stream_incomplete_terminal_event_for_request(
    request_state: _WebSocketRequestState,
    *,
    reason: str = "stream_incomplete",
) -> tuple[str, str, OpenAIEvent | None, dict[str, JsonValue] | None, str | None]:
    error_code, error_message = _websocket_continuity_error_fields(
        reason=reason,
        expose_stale_previous_response_classifier=request_state.expose_stale_previous_response_classifier,
    )
    event_block, event, payload, event_type = _build_rewritten_stream_response_failed_event(
        response_id=_websocket_downstream_response_id(request_state),
        error_code=error_code,
        error_message=error_message,
    )
    downstream_text = json.dumps(
        cast(
            dict[str, JsonValue],
            response_failed_event(
                error_code,
                error_message,
                error_type="server_error",
                response_id=_websocket_downstream_response_id(request_state),
            ),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return downstream_text, event_block, event, payload, event_type


def _slim_response_create_payload_for_upstream(
    payload: dict[str, JsonValue],
    *,
    max_bytes: int,
) -> tuple[dict[str, JsonValue], dict[str, int] | None]:
    input_value = payload.get("input")
    if not isinstance(input_value, list) or not input_value:
        return payload, None

    input_items = cast(list[JsonValue], deepcopy(input_value))
    preserve_from = _facade()._response_create_recent_suffix_start(input_items)
    historical = input_items[:preserve_from]
    recent = input_items[preserve_from:]

    tool_outputs_slimmed = 0
    images_slimmed = 0

    slimmed_historical: list[JsonValue] = []
    for item in historical:
        (
            slimmed_item,
            item_tool_outputs_slimmed,
            item_images_slimmed,
        ) = _facade()._slim_historical_response_input_item(item)
        tool_outputs_slimmed += item_tool_outputs_slimmed
        images_slimmed += item_images_slimmed
        slimmed_historical.append(slimmed_item)

    candidate_payload = dict(payload)
    candidate_payload["input"] = slimmed_historical + recent

    if tool_outputs_slimmed == 0 and images_slimmed == 0:
        return payload, None

    return candidate_payload, {
        "historical_tool_outputs_slimmed": tool_outputs_slimmed,
        "historical_images_slimmed": images_slimmed,
    }


def _call_stream_with_supported_optional_kwargs(
    func: Callable[..., AsyncIterator[str]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> AsyncIterator[str]:
    return func(*args, **_facade()._supported_optional_kwargs(func, optional_kwargs, required_kwargs))


def _stream_request_budget_seconds(settings: object, *, request_transport: str) -> float:
    if request_transport == _REQUEST_TRANSPORT_HTTP:
        budget = getattr(settings, "http_responses_stream_request_budget_seconds", None)
        if budget is not None:
            return float(budget)
    return float(getattr(settings, "proxy_request_budget_seconds"))


def _push_stream_attempt_timeout_overrides(
    timeout_seconds: float,
) -> tuple[float | None, float | None, float | None]:
    return _facade().push_stream_timeout_overrides(
        connect_timeout_seconds=timeout_seconds,
        idle_timeout_seconds=timeout_seconds,
        total_timeout_seconds=timeout_seconds,
    )


def _should_retry_stream_error(code: str) -> bool:
    return code in _facade()._ACCOUNT_RECOVERY_RETRY_CODES


def _upstream_turn_state_from_socket(upstream: UpstreamResponsesWebSocket | None) -> str | None:
    if upstream is None:
        return None
    getter = getattr(upstream, "response_header", None)
    if not callable(getter):
        return None
    value = getter("x-codex-turn-state")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
