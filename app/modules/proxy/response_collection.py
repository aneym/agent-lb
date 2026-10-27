from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import ValidationError

from app.core.openai.models import OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel
from app.core.openai.models import OpenAIResponseResult
from app.core.openai.parsing import parse_response_payload
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.sse import parse_sse_data_json
from app.modules.proxy.contract_errors import public_contract_error_envelope, public_contract_error_message
from app.modules.proxy.output_items import collect_output_item_event
from app.modules.proxy.public_payloads import normalize_public_response_mapping
from app.modules.proxy.response_errors import (
    default_error_envelope,
    error_envelope_from_response,
    looks_like_sse_data_block,
    parse_event_error_envelope,
)


async def collect_responses_payload(stream: AsyncIterator[str]) -> OpenAIResponseResult:
    output_items: dict[int, dict[str, JsonValue]] = {}
    terminal_result: OpenAIResponseResult | None = None
    contract_error_kind: str | None = None
    async for line in stream:
        payload = parse_sse_data_json(line)
        if not payload:
            if looks_like_sse_data_block(line):
                contract_error_kind = contract_error_kind or "invalid_json"
            continue
        event_type = payload.get("type")
        collect_output_item_event(payload, output_items)
        if terminal_result is not None:
            continue
        if event_type == "error":
            terminal_result = parse_event_error_envelope(payload)
            continue
        if event_type == "response.failed":
            response = payload.get("response")
            if isinstance(response, dict):
                error_value = response.get("error")
                if isinstance(error_value, dict):
                    try:
                        terminal_result = OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
                        continue
                    except ValidationError:
                        terminal_result = default_error_envelope()
                        continue
                parsed = parse_response_payload(response)
                if parsed is not None and parsed.error is not None:
                    terminal_result = error_envelope_from_response(parsed.error)
                    continue
            terminal_result = default_error_envelope()
            continue
        if event_type in ("response.completed", "response.incomplete"):
            response = payload.get("response")
            if is_json_mapping(response):
                normalized_response, violation_kind = normalize_public_response_mapping(response, output_items)
                if violation_kind is not None:
                    contract_error_kind = contract_error_kind or violation_kind
                if normalized_response is not None:
                    parsed = parse_response_payload(normalized_response)
                else:
                    parsed = None
                if parsed is not None:
                    terminal_result = parsed
                    continue
            error_kind = contract_error_kind or "invalid_json"
            terminal_result = public_contract_error_envelope(
                error_kind,
                public_contract_error_message(error_kind),
            )

    if terminal_result is not None:
        return terminal_result
    error_kind = contract_error_kind or "upstream_stream_truncated"
    return public_contract_error_envelope(
        error_kind,
        public_contract_error_message(error_kind),
    )
