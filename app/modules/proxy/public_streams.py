from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.modules.proxy.constants import PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES, PUBLIC_RESPONSES_PRE_CREATED_BUFFER_LIMIT
from app.modules.proxy.output_items import collect_output_item_event, merge_collected_output_items
from app.modules.proxy.public_payloads import (
    normalize_public_stream_payload,
    synthetic_response_created_envelope,
    synthetic_text_delta_events,
    text_delta_stream_key,
)
from app.modules.proxy.response_errors import looks_like_sse_comment_block, looks_like_sse_data_block
from app.modules.proxy.stream_replay import (
    format_buffered_pre_created_payloads,
    pre_created_buffer_has_indexed_lifecycle,
    public_response_failed_event_blocks,
    public_response_failed_event_blocks_from_error,
    response_id_from_event_payload,
    should_buffer_public_pre_created_event,
)


async def normalize_public_responses_stream(
    stream: AsyncIterator[str],
    *,
    enforce_openai_sdk_contract: bool = True,
) -> AsyncIterator[str]:

    terminal_seen = False
    done_seen = False
    contract_error_kind: str | None = None
    seen_text_delta_keys: set[tuple[str | None, int | None]] = set()

    output_items: dict[int, dict[str, JsonValue]] = {}

    created_emitted = False

    pre_created_buffer: list[dict[str, JsonValue]] = []

    def formatted_payloads_with_synthetic_deltas(payload: dict[str, JsonValue]) -> list[str]:
        return [
            *[
                format_sse_event(synthetic_payload)
                for synthetic_payload in synthetic_text_delta_events(payload, seen_text_delta_keys)
            ],
            format_sse_event(payload),
        ]

    def buffered_pre_created_payloads_to_replay(response_id: str | None) -> list[str]:
        try:
            if pre_created_buffer_has_indexed_lifecycle(pre_created_buffer):
                return []
            return format_buffered_pre_created_payloads(
                pre_created_buffer,
                response_id=response_id,
                seen_text_delta_keys=seen_text_delta_keys,
            )
        finally:
            pre_created_buffer.clear()

    async for event_block in stream:
        if event_block.strip() == "data: [DONE]":
            done_seen = True
            if terminal_seen:
                yield event_block
            continue
        if looks_like_sse_comment_block(event_block):
            yield event_block
            continue
        payload = parse_sse_data_json(event_block)
        if payload is None:
            if looks_like_sse_data_block(event_block):
                contract_error_kind = contract_error_kind or "invalid_json"
            continue
        raw_event_type = payload.get("type")
        if (
            enforce_openai_sdk_contract
            and isinstance(raw_event_type, str)
            and raw_event_type
            in (
                "response.completed",
                "response.incomplete",
            )
        ):
            response_obj = payload.get("response")
            if is_json_mapping(response_obj):
                existing_output = response_obj.get("output")
                needs_backfill = not (isinstance(existing_output, list) and existing_output)
                if needs_backfill and output_items:
                    merged_response = merge_collected_output_items(response_obj, output_items)
                    payload = dict(payload)
                    payload["response"] = merged_response
        normalized_payload, violation_kind = normalize_public_stream_payload(
            payload,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        )
        if violation_kind is not None:
            contract_error_kind = contract_error_kind or violation_kind
        if normalized_payload is None:
            continue
        event_type = normalized_payload.get("type")

        if enforce_openai_sdk_contract and not created_emitted and isinstance(event_type, str):
            if event_type == "response.created":
                created_emitted = True
                yield format_sse_event(normalized_payload)
                response_id = response_id_from_event_payload(normalized_payload)
                for formatted_payload in buffered_pre_created_payloads_to_replay(response_id):
                    yield formatted_payload
                continue

            synthetic_created = synthetic_response_created_envelope(normalized_payload)
            if synthetic_created is not None:
                yield format_sse_event(synthetic_created)
                created_emitted = True
                response_id = response_id_from_event_payload(synthetic_created)
                for formatted_payload in buffered_pre_created_payloads_to_replay(response_id):
                    yield formatted_payload
            elif should_buffer_public_pre_created_event(event_type):
                if len(pre_created_buffer) >= PUBLIC_RESPONSES_PRE_CREATED_BUFFER_LIMIT:
                    error_kind = contract_error_kind or "upstream_stream_truncated"
                    for formatted_payload in public_response_failed_event_blocks(error_kind, include_created=True):
                        yield formatted_payload
                    return
                pre_created_buffer.append(normalized_payload)
                continue
            elif event_type in PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES:
                if event_type == "error":
                    for formatted_payload in public_response_failed_event_blocks_from_error(
                        normalized_payload,
                        include_created=True,
                    ):
                        yield formatted_payload
                    return
                error_kind = contract_error_kind or "upstream_stream_truncated"
                for formatted_payload in public_response_failed_event_blocks(error_kind, include_created=True):
                    yield formatted_payload
                return

        collect_output_item_event(normalized_payload, output_items)
        if event_type == "response.output_text.delta":
            seen_text_delta_keys.add(text_delta_stream_key(normalized_payload))
        for formatted_payload in formatted_payloads_with_synthetic_deltas(normalized_payload):
            yield formatted_payload
        if isinstance(event_type, str) and event_type in PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES:
            terminal_seen = True
    if terminal_seen:
        if not done_seen and not enforce_openai_sdk_contract:
            yield "data: [DONE]\n\n"
        return
    error_kind = contract_error_kind or "upstream_stream_truncated"
    include_created = enforce_openai_sdk_contract and not created_emitted
    for formatted_payload in public_response_failed_event_blocks(error_kind, include_created=include_created):
        yield formatted_payload
