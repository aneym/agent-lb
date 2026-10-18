from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from app.core.types import JsonValue
from app.modules.proxy.tool_call_keys import (
    _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES,
    _SIDE_EFFECT_TOOL_CALL_NAMES,
    _tool_call_has_side_effect_arguments,
    canonical_json_key,
    canonical_side_effect_argument_key,
)


def replayed_side_effect_tool_call_key(item: Mapping[str, JsonValue]) -> tuple[str, str | None, str] | None:
    item_type_value = item.get("type")
    item_type = item_type_value if isinstance(item_type_value, str) else None
    if item_type == "function_call":
        item_name_value = item.get("name")
        item_name = item_name_value if isinstance(item_name_value, str) else None
        argument_value = item.get("arguments")
        if not isinstance(argument_value, str):
            return None
        is_side_effect_tool_call = item_name in _SIDE_EFFECT_TOOL_CALL_NAMES and _tool_call_has_side_effect_arguments(
            item_name,
            argument_value,
        )
        if not is_side_effect_tool_call:
            return None
        argument_key = canonical_side_effect_argument_key(item_name, argument_value)
    elif item_type == "custom_tool_call":
        item_name_value = item.get("name")
        item_name = item_name_value if isinstance(item_name_value, str) else None
        if item_name not in _SIDE_EFFECT_TOOL_CALL_NAMES:
            return None
        argument_value = item.get("input")
        if not isinstance(argument_value, str):
            return None
        argument_key = canonical_side_effect_argument_key(item_name, argument_value)
    elif item_type in _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES:
        item_name = item_type
        operation_value = item.get("operation")
        argument_key = canonical_json_key(operation_value)
    else:
        return None
    return (item_type, item_name, argument_key)


def replayed_input_segment_boundary(item: Mapping[str, JsonValue]) -> bool:
    role = item.get("role")
    if role not in {"developer", "system", "user"}:
        return False
    item_type = item.get("type")
    return item_type in {None, "message"}


def replayed_tool_call_segment_boundary(item: Mapping[str, JsonValue]) -> bool:
    item_type = item.get("type")
    return item_type in {"function_call", "custom_tool_call"} | _SIDE_EFFECT_TOOL_CALL_ITEM_TYPES


def replayed_tool_output_call_id(item: Mapping[str, JsonValue]) -> str | None:
    if item.get("type") not in {
        "function_call_output",
        "custom_tool_call_output",
        "apply_patch_call_output",
    }:
        return None
    call_id = item.get("call_id")
    if isinstance(call_id, str) and call_id:
        return call_id
    return None


def replayed_tool_output_as_assistant_message(item: Mapping[str, JsonValue]) -> JsonValue:
    output_value = item.get("output")
    if isinstance(output_value, str):
        output_text = output_value
    else:
        output_text = canonical_json_key(cast(JsonValue, output_value))
    return {
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": output_text,
            }
        ],
    }


def replayed_tool_call_without_output_as_assistant_message(item: Mapping[str, JsonValue]) -> JsonValue:
    name_value = item.get("name")
    name = name_value if isinstance(name_value, str) and name_value else "tool"
    return {
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": f"Omitted replayed side-effect tool call without matching output: {name}.",
            }
        ],
    }


def replayed_tool_output_index_for_call(
    output_indices_by_call_id: dict[str, list[int]],
    next_output_cursor_by_call_id: dict[str, int],
    *,
    call_index: int,
    call_id: str,
) -> int | None:
    output_indices = output_indices_by_call_id.get(call_id)
    if not output_indices:
        return None
    cursor = next_output_cursor_by_call_id.get(call_id, 0)
    while cursor < len(output_indices) and output_indices[cursor] < call_index:
        cursor += 1
    if cursor >= len(output_indices):
        next_output_cursor_by_call_id[call_id] = cursor
        return None
    output_index = output_indices[cursor]
    next_output_cursor_by_call_id[call_id] = cursor + 1
    return output_index
