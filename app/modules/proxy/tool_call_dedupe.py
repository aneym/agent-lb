from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import cast

from app.core.openai.models import OpenAIEvent
from app.core.openai.parsing import parse_sse_event
from app.core.types import JsonValue
from app.core.utils.sse import format_sse_event
from app.modules.proxy.tool_call_keys import (
    _PARALLEL_TOOL_CALL_NAME,
    _PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES,
    _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES,
    _SIDE_EFFECT_TOOL_CALL_NAMES,
    _tool_call_has_side_effect_arguments,
    canonical_downstream_side_effect_argument_key,
    canonical_parallel_tool_use_key,
    json_object_from_argument,
    normalize_tool_call_name,
)
from app.modules.proxy.tool_call_replay import (
    replayed_input_segment_boundary,
    replayed_side_effect_tool_call_key,
    replayed_tool_call_segment_boundary,
    replayed_tool_call_without_output_as_assistant_message,
    replayed_tool_output_as_assistant_message,
    replayed_tool_output_call_id,
    replayed_tool_output_index_for_call,
)

logger = logging.getLogger(__name__)

_TOOL_CALL_DEDUPE_CACHE_LIMIT = 1024


def event_type_from_payload(event: OpenAIEvent | None, payload: dict[str, JsonValue] | None) -> str | None:
    if event is not None:
        return event.type
    if payload is None:
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    if isinstance(payload.get("error"), dict):
        return "error"
    return None


def response_id_from_payload(payload: dict[str, JsonValue] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    top_level_response_id = payload.get("response_id")
    if isinstance(top_level_response_id, str):
        stripped = top_level_response_id.strip()
        if stripped:
            return stripped
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    response_id = response.get("id")
    if not isinstance(response_id, str):
        return None
    stripped = response_id.strip()
    return stripped or None


def mark_duplicate_tool_call_downstream_event(
    payload: dict[str, JsonValue] | None,
    *,
    seen_tool_call_keys: dict[tuple[str, str, str | None, str | None, str], None],
    response_id: str | None,
    scope_side_effects_by_response_id: bool = True,
) -> bool:
    if not isinstance(payload, dict) or payload.get("type") != "response.output_item.done":
        return False
    item = payload.get("item")
    if not isinstance(item, dict):
        return False
    item_type = item.get("type")
    if item_type == "function_call":
        argument_value = item.get("arguments")
    elif item_type == "custom_tool_call":
        argument_value = item.get("input")
    elif item_type == "apply_patch_call":
        operation_value = item.get("operation")
        if isinstance(operation_value, dict):
            argument_value = json.dumps(operation_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        else:
            argument_value = operation_value
    else:
        seen_tool_call_keys.clear()
        return False
    if not isinstance(argument_value, str):
        return False
    item_name = item.get("name")
    if item_name is not None and not isinstance(item_name, str):
        item_name = None
    call_id = item.get("call_id")
    if call_id is not None and not isinstance(call_id, str):
        call_id = None
    is_side_effect_tool_call = item_type in _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES or (
        item_name in _SIDE_EFFECT_TOOL_CALL_NAMES and _tool_call_has_side_effect_arguments(item_name, argument_value)
    )
    if not is_side_effect_tool_call:
        seen_tool_call_keys.clear()
        return False
    if item_name == _PARALLEL_TOOL_CALL_NAME and is_side_effect_tool_call:
        return _mark_duplicate_parallel_tool_call_downstream_event(
            cast(dict[str, JsonValue], item),
            argument_value,
            seen_tool_call_keys=seen_tool_call_keys,
            response_id=response_id,
            scope_side_effects_by_response_id=scope_side_effects_by_response_id,
        )
    if is_side_effect_tool_call:
        item_name = normalize_tool_call_name(item_name)
        argument_key = canonical_downstream_side_effect_argument_key(item_name, argument_value)
    else:
        argument_key = argument_value
    dedupe_response_id = response_id if response_id is not None else ""
    key = (dedupe_response_id, str(item_type), item_name, call_id, argument_key)
    if key in seen_tool_call_keys:
        logger.warning(
            "Suppressed duplicate downstream tool call response_id=%s item_type=%s name=%s",
            response_id,
            item_type,
            item_name,
        )
        return True
    if is_side_effect_tool_call:
        same_response_argument_key = (dedupe_response_id, str(item_type), item_name, None, argument_key)
        cross_response_argument_key = ("", str(item_type), item_name, None, argument_key)
        if (
            not scope_side_effects_by_response_id
            and cross_response_argument_key in seen_tool_call_keys
            and same_response_argument_key not in seen_tool_call_keys
        ):
            logger.warning(
                "Suppressed duplicate downstream side-effect replay response_id=%s item_type=%s name=%s",
                response_id,
                item_type,
                item_name,
            )
            return True
    seen_tool_call_keys[key] = None
    if is_side_effect_tool_call:
        seen_tool_call_keys[same_response_argument_key] = None
        if not scope_side_effects_by_response_id:
            seen_tool_call_keys[cross_response_argument_key] = None
    while len(seen_tool_call_keys) > _TOOL_CALL_DEDUPE_CACHE_LIMIT:
        seen_tool_call_keys.pop(next(iter(seen_tool_call_keys)))
    return False


def _mark_duplicate_parallel_tool_call_downstream_event(
    item: dict[str, JsonValue],
    argument_value: str,
    *,
    seen_tool_call_keys: dict[tuple[str, str, str | None, str | None, str], None],
    response_id: str | None,
    scope_side_effects_by_response_id: bool,
) -> bool:
    argument = json_object_from_argument(argument_value)
    if argument is None:
        return False
    tool_uses = argument.get("tool_uses")
    if not isinstance(tool_uses, list):
        return False

    kept_tool_uses: list[JsonValue] = []
    removed_count = 0
    for tool_use in tool_uses:
        if not isinstance(tool_use, dict):
            kept_tool_uses.append(cast(JsonValue, tool_use))
            continue
        recipient_name = tool_use.get("recipient_name")
        if not isinstance(recipient_name, str) or recipient_name not in _PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES:
            kept_tool_uses.append(cast(JsonValue, tool_use))
            continue
        dedupe_response_id = response_id if scope_side_effects_by_response_id else None
        key = (
            dedupe_response_id or "",
            "parallel_tool_use",
            recipient_name,
            None,
            canonical_parallel_tool_use_key(cast(dict[str, JsonValue], tool_use)),
        )
        if key in seen_tool_call_keys:
            removed_count += 1
            continue
        seen_tool_call_keys[key] = None
        kept_tool_uses.append(cast(JsonValue, tool_use))

    while len(seen_tool_call_keys) > _TOOL_CALL_DEDUPE_CACHE_LIMIT:
        seen_tool_call_keys.pop(next(iter(seen_tool_call_keys)))

    if removed_count == 0:
        return False
    logger.warning(
        "Suppressed duplicate downstream parallel tool uses response_id=%s removed=%s",
        response_id,
        removed_count,
    )
    if not kept_tool_uses:
        return True

    rewritten_argument = dict(argument)
    rewritten_argument["tool_uses"] = kept_tool_uses
    item["arguments"] = json.dumps(rewritten_argument, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return False


def dedupe_replayed_side_effect_input_items(
    input_items: list[JsonValue],
    *,
    sanitize_missing_outputs: bool = False,
) -> tuple[list[JsonValue], int]:
    call_keys: dict[int, tuple[str, str | None, str]] = {}
    call_ids: dict[int, str] = {}
    output_indices_by_call_id: dict[str, list[int]] = {}
    for index, item in enumerate(input_items):
        if not isinstance(item, dict):
            continue
        tool_call_key = replayed_side_effect_tool_call_key(item)
        if tool_call_key is not None:
            call_keys[index] = tool_call_key
            call_id = item.get("call_id")
            if isinstance(call_id, str) and call_id:
                call_ids[index] = call_id
            continue
        output_call_id = replayed_tool_output_call_id(item)
        if output_call_id is not None:
            output_indices_by_call_id.setdefault(output_call_id, []).append(index)

    if not call_keys:
        return input_items, 0

    kept = [True] * len(input_items)
    rewritten: dict[int, JsonValue] = {}
    first_call_id_by_key: dict[tuple[str, str | None, str], str | None] = {}
    first_call_index_by_key: dict[tuple[str, str | None, str], int] = {}
    output_index_by_call_index: dict[int, int | None] = {}
    next_output_cursor_by_call_id: dict[str, int] = {}
    last_side_effect_key: tuple[str, str | None, str] | None = None
    removed_count = 0
    for index, item in enumerate(input_items):
        if isinstance(item, dict) and replayed_input_segment_boundary(item):
            first_call_id_by_key.clear()
            first_call_index_by_key.clear()
            last_side_effect_key = None
        key = call_keys.get(index)
        if key is None:
            output_call_id = replayed_tool_output_call_id(item) if isinstance(item, dict) else None
            if (
                last_side_effect_key is not None
                and output_call_id is not None
                and first_call_id_by_key.get(last_side_effect_key) == output_call_id
            ):
                continue
            if output_call_id is not None or (isinstance(item, dict) and replayed_tool_call_segment_boundary(item)):
                first_call_id_by_key.clear()
                first_call_index_by_key.clear()
                last_side_effect_key = None
            continue
        if last_side_effect_key is not None and key != last_side_effect_key:
            first_call_id_by_key.clear()
        last_side_effect_key = key
        call_id = call_ids.get(index)
        output_index = (
            replayed_tool_output_index_for_call(
                output_indices_by_call_id,
                next_output_cursor_by_call_id,
                call_index=index,
                call_id=call_id,
            )
            if call_id is not None
            else None
        )
        output_index_by_call_index[index] = output_index
        if key not in first_call_id_by_key:
            first_call_id_by_key[key] = call_id
            first_call_index_by_key[key] = index
            continue

        first_call_index = first_call_index_by_key.get(key)
        if first_call_index is not None and output_index_by_call_index.get(first_call_index) is None:
            rewritten[first_call_index] = replayed_tool_call_without_output_as_assistant_message(
                cast(Mapping[str, JsonValue], input_items[first_call_index])
            )
        removed_count += 1
        kept[index] = False
        if output_index is not None:
            output_item = input_items[output_index]
            if isinstance(output_item, dict):
                rewritten[output_index] = replayed_tool_output_as_assistant_message(output_item)

    missing_output_rewrites = 0
    if sanitize_missing_outputs:
        for index, key in call_keys.items():
            del key
            if not kept[index] or index in rewritten:
                continue
            if output_index_by_call_index.get(index) is not None:
                continue
            item = input_items[index]
            if not isinstance(item, dict):
                continue
            rewritten[index] = replayed_tool_call_without_output_as_assistant_message(item)
            missing_output_rewrites += 1

    if removed_count == 0:
        if missing_output_rewrites == 0:
            return input_items, 0

    deduped_items: list[JsonValue] = []
    for index, item in enumerate(input_items):
        if kept[index]:
            deduped_items.append(rewritten.get(index, item))
    return deduped_items, removed_count + missing_output_rewrites


def dedupe_parallel_tool_uses_argument(argument_value: str) -> tuple[str, bool, int]:
    try:
        decoded_arguments = json.loads(argument_value)
    except json.JSONDecodeError:
        return argument_value, False, 0
    if not isinstance(decoded_arguments, dict):
        return argument_value, False, 0
    tool_uses = decoded_arguments.get("tool_uses")
    if not isinstance(tool_uses, list):
        return argument_value, False, 0

    seen_tool_uses: set[str] = set()
    deduped_tool_uses: list[JsonValue] = []
    removed_count = 0
    for tool_use in tool_uses:
        if not isinstance(tool_use, dict):
            deduped_tool_uses.append(cast(JsonValue, tool_use))
            continue
        recipient_name = tool_use.get("recipient_name")
        if not isinstance(recipient_name, str) or recipient_name not in _PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES:
            deduped_tool_uses.append(cast(JsonValue, tool_use))
            continue
        tool_use_key = canonical_parallel_tool_use_key(cast(dict[str, JsonValue], tool_use))
        if tool_use_key in seen_tool_uses:
            removed_count += 1
            continue
        seen_tool_uses.add(tool_use_key)
        deduped_tool_uses.append(cast(JsonValue, tool_use))

    if removed_count == 0:
        return argument_value, False, 0

    rewritten_arguments: dict[str, JsonValue] = dict(cast(dict[str, JsonValue], decoded_arguments))
    rewritten_arguments["tool_uses"] = deduped_tool_uses
    return (
        json.dumps(rewritten_arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        True,
        removed_count,
    )


def rewrite_parallel_tool_call_payload(
    payload: dict[str, JsonValue] | None,
) -> tuple[dict[str, JsonValue] | None, bool, int]:
    if not isinstance(payload, dict) or payload.get("type") != "response.output_item.done":
        return payload, False, 0
    item = payload.get("item")
    if not isinstance(item, dict):
        return payload, False, 0
    if item.get("type") != "function_call" or item.get("name") != _PARALLEL_TOOL_CALL_NAME:
        return payload, False, 0
    argument_value = item.get("arguments")
    if not isinstance(argument_value, str):
        return payload, False, 0

    rewritten_arguments, changed, removed_count = dedupe_parallel_tool_uses_argument(argument_value)
    if not changed:
        return payload, False, 0

    rewritten_item: dict[str, JsonValue] = dict(cast(dict[str, JsonValue], item))
    rewritten_item["arguments"] = rewritten_arguments
    rewritten_payload: dict[str, JsonValue] = dict(payload)
    rewritten_payload["item"] = rewritten_item
    logger.warning(
        "Suppressed duplicate nested parallel tool uses response_id=%s removed=%s",
        response_id_from_payload(rewritten_payload),
        removed_count,
    )
    return rewritten_payload, True, removed_count


def rewrite_parallel_tool_call_text(
    text: str,
    payload: dict[str, JsonValue] | None,
    *,
    event_block: str,
) -> tuple[str, dict[str, JsonValue] | None, OpenAIEvent | None, str | None, str]:
    rewritten_payload, changed, _removed_count = rewrite_parallel_tool_call_payload(payload)
    if not changed:
        event = parse_sse_event(event_block)
        return text, payload, event, event_type_from_payload(event, payload), event_block
    assert rewritten_payload is not None
    rewritten_text = json.dumps(rewritten_payload, ensure_ascii=True, separators=(",", ":"))
    rewritten_event_block = format_sse_event(rewritten_payload)
    rewritten_event = parse_sse_event(rewritten_event_block)
    return (
        rewritten_text,
        rewritten_payload,
        rewritten_event,
        event_type_from_payload(rewritten_event, rewritten_payload),
        rewritten_event_block,
    )


def rewrite_parallel_tool_call_sse_line(
    line: str,
    payload: dict[str, JsonValue] | None,
) -> tuple[str, dict[str, JsonValue] | None, OpenAIEvent | None, str | None]:
    rewritten_payload, changed, _removed_count = rewrite_parallel_tool_call_payload(payload)
    if not changed:
        event = parse_sse_event(line)
        return line, payload, event, event_type_from_payload(event, payload)
    assert rewritten_payload is not None
    rewritten_line = format_sse_event(rewritten_payload)
    rewritten_event = parse_sse_event(rewritten_line)
    return (
        rewritten_line,
        rewritten_payload,
        rewritten_event,
        event_type_from_payload(rewritten_event, rewritten_payload),
    )
