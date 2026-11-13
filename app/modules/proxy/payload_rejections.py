from __future__ import annotations

import logging
from collections.abc import Mapping

from pydantic import ValidationError

from app.core.errors import OpenAIErrorEnvelope
from app.core.openai.exceptions import ClientPayloadError
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id
from app.modules.proxy.request_policy import openai_client_payload_error, openai_validation_error

logger = logging.getLogger(__name__)

_MAX_LOGGED_ERRORS = 5


def _validation_details(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors()[:_MAX_LOGGED_ERRORS]:
        loc = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        parts.append(f"{loc or '-'}:{error.get('type')}:{error.get('msg')}")
    return "; ".join(parts)


def _payload_shape(payload: Mapping[str, JsonValue] | None) -> str:
    if payload is None:
        return "-"
    keys = ",".join(sorted(str(key) for key in payload))
    include = payload.get("include")
    include_text = ",".join(str(entry) for entry in include) if isinstance(include, list) else "-"
    input_value = payload.get("input")
    input_kind = type(input_value).__name__
    input_count = len(input_value) if isinstance(input_value, list) else "-"
    return f"keys={keys} include={include_text} input={input_kind}/{input_count}"


def rejected_payload_error(
    exc: ClientPayloadError | ValidationError,
    *,
    surface: str,
    payload: Mapping[str, JsonValue] | None,
    useragent: str | None = None,
) -> OpenAIErrorEnvelope:
    if isinstance(exc, ValidationError):
        error = openai_validation_error(exc)
        details = _validation_details(exc)
    else:
        error = openai_client_payload_error(exc)
        details = f"{exc.param or '-'}:client_payload:{exc}"
    logger.warning(
        "responses_payload_rejected request_id=%s surface=%s details=%s %s useragent=%s",
        get_request_id(),
        surface,
        details,
        _payload_shape(payload),
        useragent or "-",
    )
    return error
