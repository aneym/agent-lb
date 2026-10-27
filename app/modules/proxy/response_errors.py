from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.errors import (
    PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
    OpenAIErrorEnvelope,
    is_previous_response_not_found_error,
    openai_error,
)
from app.core.openai.models import OpenAIError
from app.core.openai.models import OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel
from app.core.resilience.overload import is_local_overload_error_code, merge_retry_after_headers
from app.core.runtime_logging import log_error_response
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.modules.proxy.constants import (
    IMAGE_ERROR_CODE_STATUS,
    IMAGE_ERROR_TYPE_STATUS,
    TRANSCRIPTION_MODEL,
    UNAVAILABLE_SELECTION_ERROR_CODES,
    logger,
)
from app.modules.proxy.selection_diagnostics import retry_after_seconds_from_error_detail


def logged_error_json_response(
    request: Request,
    status_code: int,
    content: Mapping[str, JsonValue] | OpenAIErrorEnvelopeModel | OpenAIErrorEnvelope,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    if isinstance(content, OpenAIErrorEnvelopeModel):
        public_content: Mapping[str, JsonValue] | OpenAIErrorEnvelope = content.model_dump(
            mode="json", exclude_none=True
        )
    else:
        public_content = content
    code, message = error_details_from_content(public_content)
    effective_headers = dict(headers or {})
    if status_code == 429 and is_local_overload_error_code(code):
        effective_headers = merge_retry_after_headers(effective_headers)

    if status_code in (429, 503) and "Retry-After" not in effective_headers:
        error_detail = public_content.get("error") if isinstance(public_content, Mapping) else None
        retry_after = retry_after_seconds_from_error_detail(error_detail)
        if retry_after is not None:
            effective_headers["Retry-After"] = str(retry_after)
    log_error_response(
        logger,
        request,
        status_code,
        code,
        message,
        category="proxy_error_response",
    )

    return JSONResponse(status_code=status_code, content=public_content, headers=effective_headers or None)


def error_details_from_content(
    content: Mapping[str, JsonValue] | OpenAIErrorEnvelopeModel | OpenAIErrorEnvelope,
) -> tuple[str | None, str | None]:
    if isinstance(content, OpenAIErrorEnvelopeModel):
        error = content.error
        if error is None:
            return None, None
        return error.code, error.message
    if not isinstance(content, Mapping):
        return None, None
    error = content.get("error")
    if isinstance(error, str):
        details = content.get("details")
        message = details.get("detail") if is_json_mapping(details) else None
        return error, message if isinstance(message, str) else None
    if not is_json_mapping(error):
        return None, None
    error_mapping = error
    code = error_mapping.get("code")
    message = error_mapping.get("message")
    return code if isinstance(code, str) else None, message if isinstance(message, str) else None


def looks_like_sse_data_block(event_block: str) -> bool:
    return "data:" in event_block


def looks_like_sse_comment_block(event_block: str) -> bool:
    return bool(event_block.strip()) and all(
        not line.strip() or line.lstrip().startswith(":") for line in event_block.splitlines()
    )


def parse_event_error_envelope(payload: dict[str, JsonValue]) -> OpenAIErrorEnvelopeModel:
    error_value = payload.get("error")
    if isinstance(error_value, dict):
        try:
            return OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
        except ValidationError:
            return default_error_envelope()
    return default_error_envelope()


def default_error_envelope() -> OpenAIErrorEnvelopeModel:
    return OpenAIErrorEnvelopeModel(
        error=OpenAIError(
            message="Upstream error",
            type="server_error",
            code="upstream_error",
        )
    )


def parse_error_envelope(payload: JsonValue | OpenAIErrorEnvelope) -> OpenAIErrorEnvelopeModel:
    if not isinstance(payload, dict):
        return default_error_envelope()
    if payload.get("type") == "error":
        return parse_event_error_envelope(cast(dict[str, JsonValue], payload))
    try:
        return OpenAIErrorEnvelopeModel.model_validate(payload)
    except ValidationError:
        return default_error_envelope()


def openai_invalid_transcription_model_error(model: str) -> OpenAIErrorEnvelope:
    error = openai_error(
        "invalid_request_error",
        f"Unsupported transcription model '{model}'. Only '{TRANSCRIPTION_MODEL}' is supported.",
        error_type="invalid_request_error",
    )
    error["error"]["param"] = "model"
    return error


def error_envelope_from_response(error_value: OpenAIError | None) -> OpenAIErrorEnvelopeModel:
    if error_value is None:
        return default_error_envelope()
    return OpenAIErrorEnvelopeModel(error=error_value)


def is_previous_response_not_found_public_error(error_value: OpenAIError | None) -> bool:
    if error_value is None:
        return False
    return is_previous_response_not_found_error(
        code=error_value.code,
        param=error_value.param,
        message=error_value.message,
    )


def mask_previous_response_not_found_error(
    envelope: OpenAIErrorEnvelopeModel,
    *,
    default_status: int | None = None,
) -> tuple[int, OpenAIErrorEnvelopeModel]:
    if not is_previous_response_not_found_public_error(envelope.error):
        return default_status if default_status is not None else status_for_error(envelope.error), envelope
    return (
        502,
        OpenAIErrorEnvelopeModel(
            error=OpenAIError(
                message=PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
                type="server_error",
                code="stream_incomplete",
            )
        ),
    )


def status_for_error(error_value: OpenAIError | None) -> int:
    if error_value and error_value.code == "previous_response_not_found":
        return 502
    if error_value and error_value.code in UNAVAILABLE_SELECTION_ERROR_CODES:
        return 503
    if error_value and error_value.code in {"rate_limit_exceeded", "usage_limit_reached", "insufficient_quota"}:
        return 429
    if error_value and error_value.code in {"invalid_api_key", "invalid_authentication", "token_invalidated"}:
        return 401
    if error_value and error_value.code == "invalid_request_error":
        return 400
    if error_value and error_value.type == "authentication_error":
        return 401
    if error_value and error_value.type == "invalid_request_error":
        return 400
    if error_value and error_value.type in {"rate_limit_error", "usage_limit_reached", "insufficient_quota"}:
        return 429
    return 502


def status_for_image_error_envelope(envelope: object) -> int:

    if not isinstance(envelope, Mapping):
        return 502
    error = cast(Mapping[str, object], envelope).get("error")
    if not isinstance(error, Mapping):
        return 502
    error_map = cast(Mapping[str, object], error)
    code = error_map.get("code")
    if isinstance(code, str):
        if code in IMAGE_ERROR_CODE_STATUS:
            return IMAGE_ERROR_CODE_STATUS[code]
        if code in UNAVAILABLE_SELECTION_ERROR_CODES:
            return 503
    error_type = error_map.get("type")
    if isinstance(error_type, str) and error_type in IMAGE_ERROR_TYPE_STATUS:
        return IMAGE_ERROR_TYPE_STATUS[error_type]
    return 502
