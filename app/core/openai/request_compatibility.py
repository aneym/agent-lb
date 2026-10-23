from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from app.core.eval_guard import is_eval_request
from app.core.openai.compact_budget import (
    _compact_discard_consumed_continuity_output_pairs,
    _compact_required_indices,
    _compact_state_anchor_indices,
    _compact_trim_prefix_count,
    _compact_trim_suffix_indices,
    _is_preserved_non_message_directive,
)
from app.core.openai.compact_context import (
    _compact_elide_required_tool_output_images,
    _compact_fit_selected_indices_to_wire_budget,
    _compact_has_continuity_anchor,
    _compact_item_is_side_effect_anchor,
    _compact_reconciled_tool_call_indices,
    _compact_side_effect_anchor_indices,
    _compact_trim_marker,
    _compact_trimmed_input_with_markers,
    _estimated_json_array_item_tokens,
)
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.request_input import _json_mapping_or_none
from app.core.openai.request_payload_values import (
    _EXPLICIT_PROMPT_CACHE_CONTENT_TYPES,
    _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS,
    MutableJsonObject,
    _estimated_json_tokens,
    _strip_unsupported_fields,
)
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping


def _json_parts(value: JsonValue) -> list[JsonValue]:
    if is_json_list(value):
        return value
    return [value]


UNSUPPORTED_TOOL_TYPES = {"file_search", "code_interpreter", "computer_use", "computer_use_preview", "image_generation"}
_TOOL_TYPE_ALIASES = {"web_search_preview": "web_search"}


def normalize_tool_type(tool_type: str) -> str:
    if is_eval_request() and tool_type.startswith("web_search"):
        return tool_type
    return _TOOL_TYPE_ALIASES.get(tool_type, tool_type)


def _normalize_responses_input_instructions(data: JsonValue) -> JsonValue:
    if not is_json_mapping(data):
        return data
    input_value = data.get("input")
    if not is_json_list(input_value):
        return data
    if responses_input_uses_lite_tools(input_value):
        return data
    instruction_parts: list[str] = []
    input_items: list[JsonValue] = []
    changed = False
    for item in input_value:
        item_mapping = _json_mapping_or_none(item)
        if item_mapping is None:
            input_items.append(item)
            continue
        if _is_preserved_non_message_directive(item_mapping):
            input_items.append(item)
            changed = True
            continue
        role = item_mapping.get("role")
        if role not in ("system", "developer"):
            input_items.append(item)
            continue
        instruction_text, preserved_content = _split_responses_instruction_item_content(item_mapping)
        if instruction_text:
            instruction_parts.append(instruction_text)
        if preserved_content is not None:
            preserved_item = dict(item_mapping)
            preserved_item["role"] = "user"
            preserved_item["content"] = preserved_content
            input_items.append(preserved_item)
        changed = True
    if not changed:
        return data
    normalized: MutableJsonObject = dict(data)
    existing_instructions = normalized.get("instructions")
    merged_instructions = _merge_responses_instructions(
        existing_instructions if isinstance(existing_instructions, str) else "", instruction_parts
    )
    normalized["instructions"] = merged_instructions
    normalized["input"] = input_items
    return normalized


def _is_responses_lite_input(input_value: list[JsonValue]) -> bool:
    return any(
        (
            (mapping := _json_mapping_or_none(item)) is not None and mapping.get("type") == "additional_tools"
            for item in input_value
        )
    )


def responses_input_uses_lite_tools(input_value: JsonValue) -> bool:
    return is_json_list(input_value) and _is_responses_lite_input(input_value)


def _merge_responses_instructions(existing: str, extra_parts: list[str]) -> str:
    extra = "\n".join((part for part in extra_parts if part))
    if not extra:
        return existing
    if existing:
        return f"{existing}\n{extra}"
    return extra


def _split_responses_instruction_item_content(item: Mapping[str, JsonValue]) -> tuple[str, JsonValue | None]:
    content = item.get("content")
    if content is None:
        return ("", None)
    if isinstance(content, str):
        return (content, None)
    if is_json_list(content):
        instruction_parts: list[str] = []
        preserved_parts: list[JsonValue] = []
        for part in _json_parts(content):
            text = _responses_instruction_content_text(part)
            if text is not None:
                if text:
                    instruction_parts.append(text)
                continue
            preserved_parts.append(part)
        preserved_content: JsonValue | None = preserved_parts if preserved_parts else None
        return ("\n".join(instruction_parts), preserved_content)
    text = _responses_instruction_content_text(content)
    if text is not None:
        return (text, None)
    return ("", content)


def _responses_instruction_item_text(item: Mapping[str, JsonValue]) -> str:
    instruction_text, _ = _split_responses_instruction_item_content(item)
    return instruction_text


def _responses_instruction_content_text(content: JsonValue) -> str | None:
    if isinstance(content, str):
        return content
    content_mapping = _json_mapping_or_none(content)
    if content_mapping is None:
        return None
    text = content_mapping.get("text")
    return text if isinstance(text, str) else None


def _contains_explicit_prompt_cache_breakpoint(value: JsonValue) -> bool:
    if isinstance(value, list):
        return any((_contains_explicit_prompt_cache_breakpoint(item) for item in value))
    if not isinstance(value, dict):
        return False
    value_type = value.get("type")
    if (
        isinstance(value_type, str)
        and value_type in _EXPLICIT_PROMPT_CACHE_CONTENT_TYPES
        and ("prompt_cache_breakpoint" in value)
    ):
        return True
    return any((_contains_explicit_prompt_cache_breakpoint(child) for child in value.values()))


def canonicalized_tools(tools: list[JsonValue]) -> list[JsonValue]:
    sorted_tools = sorted(tools, key=_tool_sort_key)
    return [_sort_keys_recursive(t) for t in sorted_tools]


def _canonicalize_tools(payload: MutableJsonObject) -> None:
    tools = payload.get("tools")
    if not is_json_list(tools):
        return
    tool_list = tools
    if not tool_list:
        return
    sorted_tools = sorted(tool_list, key=_tool_sort_key)
    payload["tools"] = [_sort_keys_recursive(t) for t in sorted_tools]


def _tool_sort_key(tool: JsonValue) -> str:
    if not is_json_mapping(tool):
        return ""
    tool_map = tool
    name = tool_map.get("name")
    if isinstance(name, str):
        return name
    func = tool_map.get("function")
    if is_json_mapping(func):
        func_name = func.get("name")
        if isinstance(func_name, str):
            return func_name
    return ""


def _sort_keys_recursive(value: JsonValue) -> JsonValue:
    if is_json_mapping(value):
        mapping = value
        return {k: _sort_keys_recursive(v) for k, v in sorted(mapping.items())}
    if is_json_list(value):
        return [_sort_keys_recursive(item) for item in value]
    return value


def _strip_compact_unsupported_fields(payload: MutableJsonObject) -> MutableJsonObject:
    payload = _strip_unsupported_fields(payload)
    normalized_payload = _normalize_responses_input_instructions(payload)
    if is_json_mapping(normalized_payload):
        payload = dict(normalized_payload)
    _trim_compact_input_for_upstream(payload)
    _normalize_compaction_trigger_singleton(payload)
    payload.pop("store", None)
    payload.pop("text", None)
    payload.pop("tools", None)
    payload.pop("tool_choice", None)
    payload.pop("client_metadata", None)
    payload["parallel_tool_calls"] = False
    return payload


def _normalize_compaction_trigger_singleton(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    if not is_json_list(input_value) or not input_value:
        return
    trigger_items = [item for item in input_value if is_json_mapping(item) and item.get("type") == "compaction_trigger"]
    if not trigger_items:
        return
    payload["input"] = [
        item for item in input_value if not (is_json_mapping(item) and item.get("type") == "compaction_trigger")
    ]
    cast(list[JsonValue], payload["input"]).append({"type": "compaction_trigger"})


def _trim_compact_input_for_upstream(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    if not is_json_list(input_value):
        return
    total_tokens = _estimated_json_tokens(input_value)
    if total_tokens <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        return
    has_continuity_anchor = _compact_has_continuity_anchor(payload)
    losslessly_trimmed_input = _compact_losslessly_trim_input(input_value, has_continuity_anchor=has_continuity_anchor)
    if losslessly_trimmed_input is not None:
        payload["input"] = losslessly_trimmed_input
        return
    token_counts = [_estimated_json_array_item_tokens(item) for item in input_value]
    required_indices = _compact_required_indices(input_value, token_counts, has_continuity_anchor=has_continuity_anchor)
    required_input = _compact_trimmed_input_with_markers(input_value, token_counts, required_indices)
    if _estimated_json_tokens(required_input) <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        payload["input"] = required_input
        return
    rewritten_input, images_elided = _compact_elide_required_tool_output_images(
        input_value, required_indices=required_indices
    )
    if images_elided:
        input_value = rewritten_input
        payload["input"] = input_value
        if _estimated_json_tokens(input_value) <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
            return
        losslessly_trimmed_input = _compact_losslessly_trim_input(
            input_value, has_continuity_anchor=has_continuity_anchor
        )
        if losslessly_trimmed_input is not None:
            payload["input"] = losslessly_trimmed_input
            return
    raise ClientPayloadError(
        "Compact input exceeds the upstream size limit and cannot be trimmed without removing required state anchors.",
        param="input",
        code="responses_compact_input_too_large",
    )


def _compact_losslessly_trim_input(
    input_value: list[JsonValue], *, has_continuity_anchor: bool = False
) -> list[JsonValue] | None:
    token_counts = [_estimated_json_array_item_tokens(item) for item in input_value]
    head_count = _compact_trim_prefix_count(token_counts)
    state_anchor_indices = _compact_state_anchor_indices(input_value)
    marker_tokens = _estimated_json_array_item_tokens(_compact_trim_marker(omitted_items=0, omitted_tokens=0))
    side_effect_indices = _compact_side_effect_anchor_indices(input_value)
    unusable_side_effect_indices = {
        index
        for index, item in enumerate(input_value)
        if is_json_mapping(item)
        and _compact_item_is_side_effect_anchor(item)
        and (not isinstance(item.get("call_id"), str) or not item["call_id"])
    }
    side_effect_indices = _compact_reconciled_tool_call_indices(
        input_value, side_effect_indices, token_counts=token_counts, token_budget=sum(token_counts)
    )
    required_indices = _compact_required_indices(
        input_value, token_counts, preserved_indices=state_anchor_indices, has_continuity_anchor=has_continuity_anchor
    )
    if required_indices & unusable_side_effect_indices:
        raise ClientPayloadError(
            "Compact input cannot retain a required side-effect call without a usable call_id.",
            param="input",
            code="responses_compact_input_too_large",
        )
    required_input = _compact_trimmed_input_with_markers(input_value, token_counts, required_indices)
    if _estimated_json_tokens(required_input) > _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        return None
    wire_budget = max(0, _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS - marker_tokens)
    side_effect_indices &= _compact_reconciled_tool_call_indices(
        input_value,
        required_indices | side_effect_indices,
        token_counts=token_counts,
        token_budget=wire_budget,
        required_indices=required_indices,
    )
    selected_indices = set(state_anchor_indices)
    selected_indices.update(side_effect_indices)
    selected_indices.update(range(head_count))
    selected_tokens = sum((token_counts[index] for index in selected_indices))
    tail_budget = max(0, _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS - selected_tokens - marker_tokens)
    selected_indices.update(
        _compact_trim_suffix_indices(
            token_counts, selected_indices=selected_indices, start_index=head_count, token_budget=tail_budget
        )
    )
    selected_indices.update(required_indices)
    if has_continuity_anchor:
        _compact_discard_consumed_continuity_output_pairs(
            input_value, selected_indices=selected_indices, required_indices=required_indices
        )
    selected_indices.difference_update(unusable_side_effect_indices)
    selected_indices = _compact_reconciled_tool_call_indices(
        input_value,
        selected_indices,
        token_counts=token_counts,
        token_budget=max(0, _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS - marker_tokens),
        required_indices=required_indices | side_effect_indices,
    )
    selected_indices = _compact_fit_selected_indices_to_wire_budget(
        input_value,
        token_counts,
        selected_indices=selected_indices,
        required_indices=required_indices,
        priority_indices=side_effect_indices,
    )
    trimmed_input = (
        input_value
        if len(selected_indices) == len(input_value)
        else _compact_trimmed_input_with_markers(input_value, token_counts, selected_indices)
    )
    trimmed_tokens = _estimated_json_tokens(trimmed_input)
    if trimmed_tokens > _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        return None
    return trimmed_input
