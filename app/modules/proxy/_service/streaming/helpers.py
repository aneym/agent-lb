from __future__ import annotations

import inspect
import json
import sys
from collections.abc import Callable
from copy import deepcopy
from typing import Any, AsyncIterator, Literal, Mapping, cast

from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.balancer.types import ClassifiedFailure, UpstreamError
from app.core.clients.proxy import CodexControlRequestPrivacyPolicy as CodexControlRequestPrivacyPolicy
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import (
    UpstreamWebSocket,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_NOT_FOUND_CODE as PREVIOUS_RESPONSE_NOT_FOUND_CODE,
)
from app.core.errors import (
    PREVIOUS_RESPONSE_NOT_FOUND_MESSAGE as PREVIOUS_RESPONSE_NOT_FOUND_MESSAGE,
)
from app.core.errors import PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE, response_failed_event
from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
from app.core.resilience.network_recovery import (
    PROCESS_NETWORK_UNAVAILABLE_CODE,
)
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.upstream_proxy.cache import get_upstream_route_cache
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.db.models import (
    Account,
    AccountStatus,  # noqa: F401
)
from app.modules.proxy._service.observability import _record_continuity_fail_closed as _record_continuity_fail_closed
from app.modules.proxy._service.support import (
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _REQUEST_TRANSPORT_WEBSOCKET,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _event_type_from_payload,
    _RequestLogFailureMetadata,
    _signal_propagated_capacity_startup_ready,
    _StreamSettlement,
    _WebSocketRequestState,
)
from app.modules.proxy._service.websocket.helpers import (
    _websocket_continuity_error_fields,
    _websocket_downstream_response_id,
    _websocket_event_error_code,
    _websocket_event_error_message,
    _websocket_event_error_param,
    _websocket_event_error_type,
)
from app.modules.proxy.helpers import (
    _normalize_error_code,
    classify_upstream_failure,
    is_upstream_model_capacity_error,
)
from app.modules.proxy.load_balancer import AccountSelection


def _facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


def _stream_iterator_after_capacity_admission(
    stream: AsyncIterator[str],
) -> AsyncIterator[str]:
    """Expose a slow stream once local response-create admission has succeeded."""
    _signal_propagated_capacity_startup_ready()
    return stream.__aiter__()


_REQUEST_TRANSPORT_HTTP = "http"


def _resolve_upstream_stream_transport(upstream_stream_transport: str) -> str | None:
    if upstream_stream_transport == "default":
        return None
    return upstream_stream_transport


def _should_penalize_stream_error(code: str | None) -> bool:
    if code is None:
        return False
    return code in _facade()._ACCOUNT_RECOVERY_RETRY_CODES or code in _facade()._TRANSIENT_RETRY_CODES


_MODEL_CAPACITY_LIMIT_CODES = {
    "rate_limit_exceeded",
    "usage_limit_reached",
    "insufficient_quota",
    "usage_not_included",
    "quota_exceeded",
}


def _should_retry_transient_stream_error(
    code: str | None,
    message: str | None,
    *,
    response_id: str | None = None,
) -> bool:
    if code is None or code == "stream_idle_timeout":
        return False
    if code == PROCESS_NETWORK_UNAVAILABLE_CODE:
        # Serialized terminal events arrive only after upstream dispatch. The
        # stable code keeps settlement account-neutral, but cannot prove that
        # replaying the POST is safe.
        return False
    if response_id is not None:
        return False
    if code in _facade()._TRANSIENT_RETRY_CODES:
        return True
    if is_upstream_model_capacity_error(message):
        if code in _MODEL_CAPACITY_LIMIT_CODES:
            return False
        return True
    if code != "upstream_unavailable" or not message:
        return False
    normalized_message = message.lower()
    if any(marker in normalized_message for marker in _facade()._UPSTREAM_UNAVAILABLE_NON_TRANSIENT_MESSAGE_MARKERS):
        return False
    return any(marker in normalized_message for marker in _facade()._UPSTREAM_UNAVAILABLE_TRANSIENT_MESSAGE_MARKERS)


def _classify_upstream_close(
    close_code: int | None,
    *,
    response_events_seen: int,
) -> Literal["clean", "transient"]:
    if close_code == 1000 and response_events_seen == 0:
        # A clean websocket close before response.created does not prove that
        # the request was invalid.  The upstream can close a socket during a
        # handoff, so the caller may safely recreate the socket and replay the
        # still-unstarted request once.
        return "clean"
    return "transient"


def _is_account_neutral_transport_drop(
    close_code: int | None,
    *,
    response_events_seen: int,
) -> bool:
    """Return whether an upstream websocket ending is account-neutral evidence.

    An abrupt transport drop that carries no close frame and arrived before
    any application-layer response event is the weakest possible evidence of
    account ill-health: the account never spoke at the application layer for
    this request. Charging the account lets a few infrastructure resets push
    it into error backoff and 502 continuity-bound follow-ups while healthy
    pool siblings idle (issue #1754). Any close frame — even a non-clean one —
    is upstream-authored evidence and keeps the existing penalty semantics, as
    does a drop after response events started streaming.

    Close code 1006 (abnormal closure) is reserved by RFC 6455 and can never
    appear in an actual close frame: adapters synthesize it locally when the
    socket dies without one (aiohttp stores 1006 on ``close_code`` for an
    abnormal CLOSED), so it counts as frame-less here.
    """

    return close_code in (None, 1006) and response_events_seen == 0


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


def _raw_stream_error_fields(
    event_type: str | None,
    event_payload: dict[str, JsonValue] | None,
) -> tuple[str | None, str | None, str | None, str]:
    raw_error_type = _websocket_event_error_type(event_type, event_payload)
    raw_error_message = _websocket_event_error_message(event_type, event_payload)
    raw_error_param = _websocket_event_error_param(event_type, event_payload)
    return (
        raw_error_type,
        raw_error_message,
        raw_error_param,
        _normalize_error_code(_websocket_event_error_code(event_type, event_payload), raw_error_type),
    )


def _raw_stream_error_code_or_upstream(
    event_type: str | None,
    event_payload: dict[str, JsonValue] | None,
    error_code: str,
) -> str:
    if (
        event_type == "error"
        and error_code == "error"
        and _websocket_event_error_code(event_type, event_payload) is None
    ):
        return "upstream_error"
    return error_code


def _mark_stream_settlement_interrupted(
    settlement: _StreamSettlement,
    *,
    status: str,
    error_code: str,
    error_message: str,
    failure_phase: Literal["upstream", "downstream"],
    failure_detail: str,
    account_health_error: bool,
) -> tuple[str, str, str, _RequestLogFailureMetadata]:
    settlement.record_success = False
    settlement.account_health_error = account_health_error
    settlement.error = {"message": error_message}
    return (
        status,
        error_code,
        error_message,
        _RequestLogFailureMetadata(failure_phase=failure_phase, failure_detail=failure_detail),
    )


def _mark_upstream_stream_incomplete(
    settlement: _StreamSettlement,
) -> tuple[str, str, str, _RequestLogFailureMetadata]:
    return _mark_stream_settlement_interrupted(
        settlement,
        status="error",
        error_code="stream_incomplete",
        error_message="Upstream stream ended before response.completed",
        failure_phase="upstream",
        failure_detail="upstream_eof_before_terminal_event",
        account_health_error=True,
    )


def _mark_downstream_stream_cancelled(
    settlement: _StreamSettlement,
) -> tuple[str, str, str, _RequestLogFailureMetadata]:
    return _mark_stream_settlement_interrupted(
        settlement,
        status="cancelled",
        error_code="client_disconnected",
        error_message="Downstream client disconnected before response.completed",
        failure_phase="downstream",
        failure_detail="client_disconnected_before_terminal_event",
        account_health_error=False,
    )


def _rewrite_malformed_stream_error_event(
    *,
    enforce_openai_sdk_contract: bool,
    event: OpenAIEvent | None,
    event_type: str | None,
    event_payload: dict[str, JsonValue] | None,
    response_id: str,
) -> tuple[str, OpenAIEvent | None, dict[str, JsonValue] | None, str | None] | None:
    """Rewrite a schema-less upstream ``error`` frame under the SDK contract.

    A malformed frame like ``{"type":"error","message":"..."}`` classifies as
    ``error`` but carries no error envelope (``event`` is None or has no
    ``error``), so it must become a terminal ``response.failed`` instead of
    leaking the raw frame with a success settlement. Returns None when the
    frame is not a malformed error (well-formed errors keep their
    envelope-driven handling).
    """
    if not enforce_openai_sdk_contract or event_type != "error":
        return None
    if (event is not None and event.error is not None) or not isinstance(event_payload, dict):
        return None
    message_value = event_payload.get("message")
    message = message_value.strip() if isinstance(message_value, str) and message_value.strip() else "Upstream error"
    return _build_rewritten_stream_response_failed_event(
        response_id=response_id,
        error_code="upstream_error",
        error_message=message,
    )


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
    del request_transport
    budget = getattr(settings, "http_responses_stream_request_budget_seconds", None)
    if budget is not None:
        return float(budget)
    return float(getattr(settings, "proxy_request_budget_seconds"))


async def _resolve_upstream_route_for_account(
    proxy: Any,
    account: Account,
    *,
    operation: str,
) -> ResolvedUpstreamRoute | None:
    # Outcomes (route / direct-egress None / fail-closed error) are cached
    # verbatim, so a hit can never change the degradation path the resolver
    # chose; errors re-raise with their original reason.
    cache = get_upstream_route_cache()
    cached = cache.get(account.id)
    if cached is not None:
        return cached.unwrap(account.id)
    generation = cache.generation
    async with _facade().SessionLocal() as session:
        try:
            route = await _facade().resolve_upstream_route(
                session,
                account_id=account.id,
                operation=operation,
                scope="account",
                encryptor=proxy._encryptor,
            )
        except UpstreamProxyRouteError as exc:
            cache.store_error(account.id, exc, generation=generation)
            raise
    cache.store_route(account.id, route, generation=generation)
    return route


async def _select_account_with_budget_for_stream(proxy: Any, deadline: float, **kwargs: Any) -> AccountSelection:
    selector = proxy._select_account_with_budget_compatible
    optional_kwargs = (
        "require_security_work_authorized",
        "lease_kind",
        "estimated_lease_tokens",
        "fallback_on_preferred_account_unavailable",
        "preferred_account_is_continuity_owner",
        "spill_bare_session_on_account_cap",
        "require_unambiguous_account",
    )
    if any(name in kwargs for name in optional_kwargs):
        try:
            signature = inspect.signature(selector)
        except (TypeError, ValueError):
            signature = None
        accepts_var_keyword = signature is not None and any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
        )
        if signature is not None and not accepts_var_keyword:
            kwargs = dict(kwargs)
            for name in optional_kwargs:
                if name not in signature.parameters:
                    kwargs.pop(name, None)
    return await selector(deadline, **kwargs)


def _is_account_neutral_request_rejection(
    *,
    code: str,
    http_status: int | None,
    message: str | None,
) -> bool:
    """Return whether upstream rejected the request payload, not the account.

    A payload-shape rejection reproduces identically on every account, so it
    must never mutate one account's health: otherwise a single client looping
    on a self-inconsistent conversation drives its serving accounts into
    ``error_count`` backoff and starves unrelated tenants.

    Keep this set narrow. Not every upstream ``invalid_request_error`` is
    account neutral -- the model-entitlement rejection matched by
    ``_is_account_model_unsupported_error`` is genuinely account scoped -- so
    membership is decided by the specific classified message, never by the
    ``invalid_request_error`` code alone.
    """
    if code != "invalid_request_error":
        return False
    if http_status is not None and http_status != 400:
        return False
    return bool(_facade()._is_missing_tool_output_message(message))


async def _handle_stream_error(
    proxy: Any,
    account: Account,
    error: UpstreamError,
    code: str,
    http_status: int | None = None,
    *,
    privacy_policy: CodexControlRequestPrivacyPolicy = CodexControlRequestPrivacyPolicy.STANDARD,
) -> ClassifiedFailure:
    classified = classify_upstream_failure(
        error_code=code,
        error=error,
        http_status=http_status,
        phase="first_event",
    )
    if _facade()._is_account_neutral_error_code(code):
        return classified
    if _is_account_neutral_request_rejection(
        code=code,
        http_status=http_status,
        message=error.get("message"),
    ):
        _facade().logger.info(
            "Skipped account error penalty for account-neutral request rejection account_id=%s request_id=%s code=%s",
            "<redacted>" if privacy_policy.redacts_sensitive_details else account.id,
            get_request_id(),
            code,
        )
        return classified
    if classified["failure_class"] == "rate_limit":
        await proxy._load_balancer.mark_rate_limit(account, error)
    elif classified["failure_class"] == "quota":
        await proxy._load_balancer.mark_quota_exceeded(account, error)
    elif code in PERMANENT_FAILURE_CODES:
        await proxy._load_balancer.mark_permanent_failure(account, code)
    else:
        await proxy._load_balancer.record_error(account)
        _facade().logger.info(
            "Recorded transient account error account_id=%s request_id=%s code=%s",
            "<redacted>" if privacy_policy.redacts_sensitive_details else account.id,
            get_request_id(),
            code,
        )
    return classified


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


def _upstream_turn_state_from_socket(upstream: UpstreamWebSocket | None) -> str | None:
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
