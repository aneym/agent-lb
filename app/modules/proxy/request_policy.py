from __future__ import annotations

import logging

from pydantic import ValidationError

from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.exceptions import ProxyModelNotAllowed
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.model_registry import ModelRegistry, get_model_registry
from app.core.openai.requests import ResponsesCompactRequest, ResponsesReasoning, ResponsesRequest
from app.core.openai.strict_schema import (
    validate_strict_function_tool_schema,
    validate_strict_json_schema,
)
from app.core.openai.v1_requests import V1ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id
from app.modules.api_keys.service_data import ApiKeyData

logger = logging.getLogger(__name__)


_UNSUPPORTED_UPSTREAM_REASONING_EFFORTS: frozenset[str] = frozenset({"minimal"})
_DEFAULT_REASONING_EFFORT_FALLBACK = "low"


_GPT5_ALIAS_BASE_MODELS: tuple[str, ...] = (
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "gpt-5.1-codex",
    "gpt-5-codex",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
)
_MODEL_ALIAS_REASONING_TOKENS: dict[str, str] = {
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "extra": "high",
}
_MODEL_ALIAS_REASONING_RANK: dict[str, int] = {"minimal": 0, "low": 1, "medium": 2, "high": 3}
_MODEL_ALIAS_SERVICE_TIER_TOKENS: dict[str, str] = {
    "fast": "priority",
    "priority": "priority",
}
_MODEL_ALIAS_IGNORED_TOKENS: frozenset[str] = frozenset({"reasoning", "thinking"})
_MODEL_ALIAS_TOKENS: frozenset[str] = frozenset(
    {
        *tuple(_MODEL_ALIAS_REASONING_TOKENS),
        *tuple(_MODEL_ALIAS_SERVICE_TIER_TOKENS),
        *tuple(_MODEL_ALIAS_IGNORED_TOKENS),
    }
)


_UPSTREAM_OMIT_SERVICE_TIERS: frozenset[str] = frozenset({"auto", "default"})


def validate_model_access(api_key: ApiKeyData | None, model: str | None) -> None:
    if api_key is None:
        return
    if not api_key.allowed_models:
        return
    allowed_models = {resolve_model_alias(allowed_model) for allowed_model in api_key.allowed_models}
    effective_model = resolve_model_alias(model)
    if model is None or effective_model in allowed_models:
        return
    raise ProxyModelNotAllowed(f"This API key does not have access to model '{model}'")


def apply_api_key_enforcement(
    payload: ResponsesRequest | ResponsesCompactRequest,
    api_key: ApiKeyData | None,
) -> None:
    normalize_upstream_model_alias(payload)

    if api_key is None:
        normalize_unsupported_reasoning_effort(payload)
        return

    if api_key.enforced_model and payload.model != api_key.enforced_model:
        logger.info(
            "api_key_model_enforced request_id=%s key_id=%s requested_model=%s enforced_model=%s",
            get_request_id(),
            api_key.id,
            payload.model,
            api_key.enforced_model,
        )
        payload.model = api_key.enforced_model
        normalize_upstream_model_alias(payload)

    if api_key.enforced_reasoning_effort is not None:
        requested_effort = payload.reasoning.effort if payload.reasoning else None
        if payload.reasoning is None:
            payload.reasoning = ResponsesReasoning(effort=api_key.enforced_reasoning_effort)
        else:
            payload.reasoning.effort = api_key.enforced_reasoning_effort
        if requested_effort != api_key.enforced_reasoning_effort:
            logger.info(
                "api_key_reasoning_enforced request_id=%s key_id=%s requested_effort=%s enforced_effort=%s",
                get_request_id(),
                api_key.id,
                requested_effort,
                api_key.enforced_reasoning_effort,
            )

    normalize_unsupported_reasoning_effort(payload)

    if api_key.enforced_service_tier is not None:
        requested_service_tier = getattr(payload, "service_tier", None)

        if api_key.enforced_service_tier in _UPSTREAM_OMIT_SERVICE_TIERS:
            effective_service_tier: str | None = None
        else:
            effective_service_tier = api_key.enforced_service_tier
        setattr(payload, "service_tier", effective_service_tier)
        if requested_service_tier != api_key.enforced_service_tier:
            logger.info(
                "api_key_service_tier_enforced request_id=%s key_id=%s "
                "requested_service_tier=%s enforced_service_tier=%s "
                "outbound_service_tier=%s",
                get_request_id(),
                api_key.id,
                requested_service_tier,
                api_key.enforced_service_tier,
                effective_service_tier,
            )


def resolve_model_alias(model: str | None) -> str | None:
    alias = _resolve_model_alias_parts(model)
    if alias is None:
        return model
    return alias[0]


def strip_model_alias_suffix(model: str) -> str:

    parts = model.strip().lower().split("-")
    while len(parts) > 1 and parts[-1] in _MODEL_ALIAS_TOKENS:
        parts.pop()
    return "-".join(parts)


def normalize_upstream_model_alias(payload: ResponsesRequest | ResponsesCompactRequest) -> None:
    requested_model = payload.model
    alias = _resolve_model_alias_parts(requested_model)
    if alias is None:
        return

    canonical_model, alias_effort, alias_service_tier = alias
    if payload.model != canonical_model:
        logger.info(
            "model_alias_normalized request_id=%s requested_model=%s normalized_model=%s",
            get_request_id(),
            payload.model,
            canonical_model,
        )
        payload.model = canonical_model

    if alias_effort is not None:
        requested_effort = payload.reasoning.effort if payload.reasoning else None
        if payload.reasoning is None:
            payload.reasoning = ResponsesReasoning(effort=alias_effort)
        else:
            payload.reasoning.effort = alias_effort
        if requested_effort != alias_effort:
            logger.info(
                "model_alias_reasoning_normalized request_id=%s requested_model=%s "
                "normalized_model=%s requested_effort=%s normalized_effort=%s",
                get_request_id(),
                requested_model,
                canonical_model,
                requested_effort,
                alias_effort,
            )

    if alias_service_tier is not None and getattr(payload, "service_tier", None) is None:
        setattr(payload, "service_tier", alias_service_tier)
        logger.info(
            "model_alias_service_tier_normalized request_id=%s requested_model=%s "
            "normalized_model=%s normalized_service_tier=%s",
            get_request_id(),
            requested_model,
            canonical_model,
            alias_service_tier,
        )


def _resolve_model_alias_parts(model: str | None) -> tuple[str, str | None, str | None] | None:
    if not isinstance(model, str):
        return None
    normalized = model.strip().lower()
    if not normalized:
        return None

    for base_model in _GPT5_ALIAS_BASE_MODELS:
        prefix = f"{base_model}-"
        if not normalized.startswith(prefix):
            continue
        suffix = normalized[len(prefix) :]
        tokens = [token for token in suffix.split("-") if token]
        if not tokens or any(token not in _MODEL_ALIAS_TOKENS for token in tokens):
            return None
        return base_model, _resolve_alias_reasoning_effort(tokens), _resolve_alias_service_tier(tokens)

    return None


def _resolve_alias_reasoning_effort(tokens: list[str]) -> str | None:
    selected: str | None = None
    selected_rank = -1
    for token in tokens:
        effort = _MODEL_ALIAS_REASONING_TOKENS.get(token)
        if effort is None:
            continue
        rank = _MODEL_ALIAS_REASONING_RANK[effort]
        if rank > selected_rank:
            selected = effort
            selected_rank = rank
    return selected


def _resolve_alias_service_tier(tokens: list[str]) -> str | None:
    for token in tokens:
        service_tier = _MODEL_ALIAS_SERVICE_TIER_TOKENS.get(token)
        if service_tier is not None:
            return service_tier
    return None


def normalize_unsupported_reasoning_effort(
    payload: ResponsesRequest | ResponsesCompactRequest,
    *,
    registry: ModelRegistry | None = None,
) -> None:

    if payload.reasoning is None or payload.reasoning.effort is None:
        return

    requested_effort = payload.reasoning.effort
    normalized_effort = requested_effort.strip().lower()
    if normalized_effort not in _UNSUPPORTED_UPSTREAM_REASONING_EFFORTS:
        return

    fallback = _resolve_reasoning_effort_fallback(
        payload.model,
        registry=registry or get_model_registry(),
    )
    payload.reasoning.effort = fallback
    logger.info(
        "reasoning_effort_normalized request_id=%s model=%s requested_effort=%s normalized_effort=%s",
        get_request_id(),
        payload.model,
        requested_effort,
        fallback,
    )


def _resolve_reasoning_effort_fallback(
    model: str | None,
    *,
    registry: ModelRegistry,
) -> str:
    if not model:
        return _DEFAULT_REASONING_EFFORT_FALLBACK
    snapshot = registry.get_snapshot()
    if snapshot is None:
        return _DEFAULT_REASONING_EFFORT_FALLBACK
    upstream = snapshot.models.get(model) or snapshot.models.get(model.strip().lower())
    if upstream is None:
        return _DEFAULT_REASONING_EFFORT_FALLBACK
    advertised = [level.effort for level in upstream.supported_reasoning_levels if level.effort]

    for effort in advertised:
        if effort.strip().lower() not in _UNSUPPORTED_UPSTREAM_REASONING_EFFORTS:
            return effort
    return _DEFAULT_REASONING_EFFORT_FALLBACK


def openai_validation_error(exc: ValidationError) -> OpenAIErrorEnvelope:
    error = openai_invalid_payload_error()
    if exc.errors():
        first = exc.errors()[0]
        loc = first.get("loc", [])
        if isinstance(loc, (list, tuple)):
            param = ".".join(str(part) for part in loc if part != "body")
            if param:
                error["error"]["param"] = param
    return error


def openai_invalid_payload_error(param: str | None = None) -> OpenAIErrorEnvelope:
    error = openai_error("invalid_request_error", "Invalid request payload", error_type="invalid_request_error")
    if param:
        error["error"]["param"] = param
    return error


def openai_client_payload_error(exc: ClientPayloadError) -> OpenAIErrorEnvelope:

    if exc.code is None and exc.error_type is None:
        return openai_invalid_payload_error(exc.param)
    code = exc.code or "invalid_request_error"
    error_type = exc.error_type or "invalid_request_error"
    error = openai_error(code, str(exc), error_type=error_type)
    if exc.param:
        error["error"]["param"] = exc.param
    return error


def normalize_responses_request_payload(
    payload: dict[str, JsonValue],
    *,
    openai_compat: bool,
) -> ResponsesRequest:
    if openai_compat:
        responses = V1ResponsesRequest.model_validate(payload).to_responses_request()
    else:
        responses = ResponsesRequest.model_validate(payload)
    enforce_strict_text_format(responses)
    enforce_strict_function_tools_format(responses.tools)
    return responses


def enforce_strict_text_format(request: ResponsesRequest) -> None:

    if request.text is None or request.text.format is None:
        return
    text_format = request.text.format
    if text_format.type != "json_schema" or text_format.strict is not True:
        return
    if text_format.schema_ is None:
        return
    violation = validate_strict_json_schema(
        text_format.schema_,
        name=text_format.name,
        param="text.format.schema",
    )
    if violation is None:
        return
    raise ClientPayloadError(
        violation.message,
        param=violation.param,
        code=violation.code,
        error_type="invalid_request_error",
    )


def enforce_strict_function_tools_format(
    tools: list[JsonValue] | None,
    *,
    param_template: str = "tools[{index}].parameters",
    nested: bool = False,
) -> None:

    if not tools:
        return
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue
        if nested:
            function_value = tool.get("function")
            if not isinstance(function_value, dict):
                continue
            descriptor = function_value
        else:
            if tool.get("type") != "function":
                continue
            descriptor = tool
        if descriptor.get("strict") is not True:
            continue
        parameters = descriptor.get("parameters")
        if parameters is None:
            continue
        raw_name = descriptor.get("name")
        name = raw_name if isinstance(raw_name, str) else None
        violation = validate_strict_function_tool_schema(
            parameters,
            name=name,
            param=param_template.format(index=index),
        )
        if violation is None:
            continue
        raise ClientPayloadError(
            violation.message,
            param=violation.param,
            code=violation.code,
            error_type="invalid_request_error",
        )
