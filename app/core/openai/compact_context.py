from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import cast

from app.core.openai.request_input import _json_mapping_or_none
from app.core.openai.request_payload_values import (
    _COMPACT_INLINE_IMAGE_DATA_URL_RE,
    _COMPACT_OMITTED_INLINE_IMAGE_TEXT,
    _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES,
    _COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE,
    _ESTIMATED_CHARS_PER_TOKEN,
    _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS,
    _estimated_json_tokens,
)
from app.core.openai.tool_call_safety import is_downstream_side_effect_tool_call_item
from app.core.types import JsonObject, JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

_COMPACT_TOOL_CALL_ITEM_TYPES = frozenset(_COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE.values())


def _compact_has_continuity_anchor(payload: Mapping[str, JsonValue]) -> bool:
    return any(
        (
            isinstance(payload.get(field), str) and bool(cast(str, payload[field]).strip())
            for field in ("previous_response_id", "conversation")
        )
    )


def _compact_matching_tool_call_index(input_value: list[JsonValue], output_index: int) -> int | None:
    output = _json_mapping_or_none(input_value[output_index])
    if output is None:
        return None
    call_id = output.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return None
    output_type = output.get("type")
    expected_call_type = (
        _COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(output_type) if isinstance(output_type, str) else None
    )
    if expected_call_type is None:
        return None
    unmatched_call_indices: list[int] = []
    for index in range(output_index):
        item = _json_mapping_or_none(input_value[index])
        if item is None or item.get("call_id") != call_id:
            continue
        if item.get("type") == expected_call_type:
            unmatched_call_indices.append(index)
        elif item.get("type") == output_type and unmatched_call_indices:
            unmatched_call_indices.pop()
    return unmatched_call_indices[-1] if unmatched_call_indices else None


def _compact_elide_required_tool_output_images(
    input_value: list[JsonValue], *, required_indices: set[int]
) -> tuple[list[JsonValue], bool]:
    rewritten: list[JsonValue] = []
    changed = False
    for index, item in enumerate(input_value):
        if (
            index in required_indices
            and is_json_mapping(item)
            and (item.get("type") in _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES)
        ):
            rewritten_item, item_changed = _compact_elide_inline_images(item)
            rewritten.append(rewritten_item)
            changed = changed or item_changed
        else:
            rewritten.append(item)
    return (rewritten, changed)


def _compact_elide_inline_images(value: JsonValue) -> tuple[JsonValue, bool]:
    if is_json_mapping(value):
        if value.get("type") == "input_image":
            image_url = value.get("image_url")
            if isinstance(image_url, str) and image_url.startswith("data:image/"):
                return (
                    {
                        "type": "input_text",
                        "text": f"{_COMPACT_OMITTED_INLINE_IMAGE_TEXT} ({len(image_url)} encoded characters).",
                    },
                    True,
                )
        if value.get("type") == "image_url":
            image_url = value.get("image_url")
            url = image_url.get("url") if is_json_mapping(image_url) else image_url
            if isinstance(url, str) and url.startswith("data:image/"):
                return (
                    {"type": "text", "text": f"{_COMPACT_OMITTED_INLINE_IMAGE_TEXT} ({len(url)} encoded characters)."},
                    True,
                )
        rewritten_mapping: JsonObject = {}
        changed = False
        for key, item in value.items():
            rewritten_item, item_changed = _compact_elide_inline_images(item)
            rewritten_mapping[key] = rewritten_item
            changed = changed or item_changed
        return (rewritten_mapping, changed)
    if is_json_list(value):
        rewritten: list[JsonValue] = []
        changed = False
        for item in value:
            rewritten_item, item_changed = _compact_elide_inline_images(item)
            rewritten.append(rewritten_item)
            changed = changed or item_changed
        return (rewritten, changed)
    if isinstance(value, str) and "data:image/" in value:
        rewritten_value, replacements = _COMPACT_INLINE_IMAGE_DATA_URL_RE.subn(
            lambda match: f"{_COMPACT_OMITTED_INLINE_IMAGE_TEXT} ({len(match.group(0))} encoded characters).", value
        )
        if replacements:
            return (rewritten_value, True)
    return (value, False)


def _compact_fit_selected_indices_to_wire_budget(
    input_value: list[JsonValue],
    token_counts: list[int],
    *,
    selected_indices: set[int],
    required_indices: set[int],
    priority_indices: set[int] | None = None,
) -> set[int]:
    selected = set(selected_indices)
    prioritized = priority_indices or set()

    def optional_drop_key(index: int) -> tuple[int, int, int]:
        if index in prioritized:
            return (0, 0, -index)
        return (1, min(index, len(input_value) - 1 - index), index)

    optional_indices = sorted(selected - required_indices, key=optional_drop_key, reverse=True)
    marker_budget = max(
        0,
        _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS
        - _estimated_json_array_item_tokens(_compact_trim_marker(omitted_items=0, omitted_tokens=0)),
    )
    for index in optional_indices:
        candidate_input = _compact_trimmed_input_with_markers(input_value, token_counts, selected)
        if _estimated_json_tokens(candidate_input) <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
            break
        if index not in selected:
            continue
        trial = set(selected)
        trial.remove(index)
        trial = _compact_reconciled_tool_call_indices(
            input_value,
            trial,
            token_counts=token_counts,
            token_budget=marker_budget,
            required_indices=required_indices,
            allow_pair_additions=False,
        )
        if required_indices <= trial and trial != selected:
            selected = trial
    return selected


def _compact_side_effect_anchor_indices(input_value: list[JsonValue]) -> set[int]:
    preserved_indices: set[int] = set()
    for index, item in enumerate(input_value):
        if (
            is_json_mapping(item)
            and _compact_item_is_side_effect_anchor(item)
            and isinstance(item.get("call_id"), str)
            and item["call_id"]
        ):
            preserved_indices.add(index)
    return preserved_indices


def _compact_reconciled_tool_call_indices(
    input_value: list[JsonValue],
    selected_indices: set[int],
    *,
    token_counts: list[int],
    token_budget: int,
    required_indices: set[int] | None = None,
    allow_pair_additions: bool = True,
) -> set[int]:
    call_indices_by_key: dict[tuple[str, str], list[int]] = {}
    output_indices_by_key: dict[tuple[str, str], list[int]] = {}
    for index, item in enumerate(input_value):
        if not is_json_mapping(item):
            continue
        call_id = item.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type in _COMPACT_TOOL_CALL_ITEM_TYPES:
            call_indices_by_key.setdefault((call_id, item_type), []).append(index)
        elif isinstance(item_type, str):
            matching_call_type = _COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(item_type)
            if matching_call_type is not None:
                output_indices_by_key.setdefault((call_id, matching_call_type), []).append(index)
    reconciled = set(selected_indices)
    protected_indices = required_indices or set()
    selected_tokens = sum((token_counts[index] for index in reconciled))

    def add_indices(indices: Iterable[int]) -> bool:
        nonlocal selected_tokens
        missing_indices = [index for index in indices if index not in reconciled]
        missing_tokens = sum((token_counts[index] for index in missing_indices))
        if selected_tokens + missing_tokens > token_budget:
            return False
        reconciled.update(missing_indices)
        selected_tokens += missing_tokens
        return True

    def remove_indices(indices: Iterable[int]) -> None:
        nonlocal selected_tokens
        for index in indices:
            if index in reconciled and index not in protected_indices:
                reconciled.remove(index)
                selected_tokens -= token_counts[index]

    def matching_call_index(call_indices: list[int], output_indices: list[int], output_index: int) -> int | None:
        unmatched_calls: list[int] = []
        call_index_set = set(call_indices)
        output_index_set = set(output_indices)
        for index in range(output_index):
            if index in call_index_set:
                unmatched_calls.append(index)
            elif index in output_index_set and unmatched_calls:
                unmatched_calls.pop()
        return unmatched_calls[-1] if unmatched_calls else None

    def matching_output_indices(call_indices: list[int], call_index: int, output_indices: list[int]) -> list[int]:
        next_call_indices = [next_call_index for next_call_index in call_indices if next_call_index > call_index]
        next_call_index = next_call_indices[0] if next_call_indices else None
        return [
            output_index
            for output_index in output_indices
            if output_index > call_index and (next_call_index is None or output_index < next_call_index)
        ][:1]

    for pair_key, output_indices in output_indices_by_key.items():
        selected_outputs = [index for index in output_indices if index in reconciled]
        if not selected_outputs:
            continue
        call_indices = call_indices_by_key.get(pair_key, [])
        for output_index in selected_outputs:
            call_index = matching_call_index(call_indices, output_indices, output_index)
            if call_index is None:
                remove_indices([output_index])
            elif not allow_pair_additions and call_index not in reconciled:
                remove_indices([output_index])
            elif not add_indices([call_index]):
                remove_indices([output_index])
    for pair_key, call_indices in call_indices_by_key.items():
        output_indices = output_indices_by_key.get(pair_key, [])
        for call_index in call_indices:
            if call_index not in reconciled:
                continue
            matched_output_indices = matching_output_indices(call_indices, call_index, output_indices)
            if not matched_output_indices:
                remove_indices([call_index])
                continue
            if not allow_pair_additions and any((index not in reconciled for index in matched_output_indices)):
                remove_indices([call_index, *matched_output_indices])
                continue
            if not add_indices(matched_output_indices):
                remove_indices([call_index, *matched_output_indices])
    return reconciled


def _compact_item_is_side_effect_anchor(item: Mapping[str, JsonValue]) -> bool:
    return is_downstream_side_effect_tool_call_item(item)


def _compact_trimmed_input_with_markers(
    input_value: list[JsonValue], token_counts: list[int], selected_indices: set[int]
) -> list[JsonValue]:
    trimmed: list[JsonValue] = []
    omitted_items = 0
    omitted_tokens = 0
    for index, item in enumerate(input_value):
        if index in selected_indices:
            if omitted_items:
                trimmed.append(_compact_trim_marker(omitted_items=omitted_items, omitted_tokens=omitted_tokens))
                omitted_items = 0
                omitted_tokens = 0
            trimmed.append(item)
        else:
            omitted_items += 1
            omitted_tokens += token_counts[index]
    if omitted_items:
        trimmed.append(_compact_trim_marker(omitted_items=omitted_items, omitted_tokens=omitted_tokens))
    return trimmed


def _compact_trim_marker(*, omitted_items: int, omitted_tokens: int) -> JsonObject:
    return {
        "type": "message",
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": f"[compact trim] Omitted {omitted_items} input items (~{omitted_tokens} estimated tokens) before forwarding this oversized compact request upstream. Required compact state anchors and retained input items remain in their original order; omitted items may include terminal context.",
            }
        ],
    }


def _estimated_json_array_item_tokens(value: JsonValue) -> int:
    serialized = json.dumps(value, ensure_ascii=True, sort_keys=True)
    wire_chars = len(serialized) + 2
    return max(1, (wire_chars + _ESTIMATED_CHARS_PER_TOKEN - 1) // _ESTIMATED_CHARS_PER_TOKEN)
