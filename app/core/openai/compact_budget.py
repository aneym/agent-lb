from __future__ import annotations

from collections.abc import Mapping

from app.core.openai.compact_context import (
    _COMPACT_TOOL_CALL_ITEM_TYPES,
    _compact_elide_inline_images,
    _compact_item_is_side_effect_anchor,
    _compact_matching_tool_call_index,
    _compact_reconciled_tool_call_indices,
    _compact_trimmed_input_with_markers,
)
from app.core.openai.request_input import _json_mapping_or_none
from app.core.openai.request_payload_values import (
    _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES,
    _COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE,
    _GOAL_CONTINUATION_CONTEXT_PREFIX,
    _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS,
    _compact_item_texts,
    _estimated_json_tokens,
)
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping

_COMPACT_STATE_TOOL_NAMES = frozenset({"create_goal", "get_goal", "update_goal", "update_plan"})
_COMPACT_SIDE_EFFECT_TOOL_ITEM_TYPES = frozenset({"apply_patch_call", "apply_patch_call_output"})
_PLAN_MODE_CONTEXT_PREFIX = "<collaboration_mode># Plan Mode"


def _is_preserved_non_message_directive(item: Mapping[str, JsonValue]) -> bool:
    if item.get("role") not in ("system", "developer"):
        return False
    item_type = item.get("type")
    return item_type is not None and item_type != "message"


_COMPACT_UPSTREAM_HEAD_ESTIMATED_TOKENS = 12000


def _compact_required_indices(
    input_value: list[JsonValue],
    token_counts: list[int],
    *,
    preserved_indices: set[int] | None = None,
    has_continuity_anchor: bool = False,
) -> set[int]:
    if preserved_indices is None:
        preserved_indices = _compact_state_anchor_indices(input_value)
    required_indices = _compact_reconciled_tool_call_indices(
        input_value,
        preserved_indices,
        token_counts=token_counts,
        token_budget=sum(token_counts),
        required_indices=preserved_indices,
    )
    terminal_indices, terminal_is_required, reconcile_terminal_pairs = _compact_terminal_required_indices(
        input_value, token_counts=token_counts, has_continuity_anchor=has_continuity_anchor
    )
    if terminal_indices:
        prospective_required_indices = required_indices | terminal_indices
        if reconcile_terminal_pairs:
            prospective_required_indices = _compact_reconciled_tool_call_indices(
                input_value,
                prospective_required_indices,
                token_counts=token_counts,
                token_budget=sum(token_counts),
                required_indices=prospective_required_indices,
            )
        prospective_required_input = _compact_trimmed_input_with_markers(
            input_value, token_counts, prospective_required_indices
        )
        if (
            terminal_is_required
            or _estimated_json_tokens(prospective_required_input) <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS
        ):
            required_indices = prospective_required_indices
    return required_indices


def _compact_discard_consumed_continuity_output_pairs(
    input_value: list[JsonValue], *, selected_indices: set[int], required_indices: set[int]
) -> None:
    if not input_value:
        return
    latest_index = len(input_value) - 1
    latest_mapping = _json_mapping_or_none(input_value[latest_index])
    latest_type = latest_mapping.get("type") if latest_mapping is not None else None
    if latest_type == "compaction_trigger":
        if latest_index == 0:
            return
        latest_index -= 1
        latest_mapping = _json_mapping_or_none(input_value[latest_index])
        latest_type = latest_mapping.get("type") if latest_mapping is not None else None
    if (
        latest_mapping is None
        or not isinstance(latest_type, str)
        or latest_type not in _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES
        or (_compact_matching_tool_call_index(input_value, latest_index) is not None)
    ):
        return
    latest_call_id = latest_mapping.get("call_id")
    matching_call_type = _COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(latest_type)
    selected_indices.difference_update(
        (
            index
            for index, item in enumerate(input_value[:latest_index])
            if is_json_mapping(item)
            and item.get("call_id") == latest_call_id
            and (item.get("type") in {latest_type, matching_call_type})
            and (index not in required_indices)
        )
    )


def _compact_terminal_required_indices(
    input_value: list[JsonValue], *, token_counts: list[int], has_continuity_anchor: bool
) -> tuple[set[int], bool, bool]:
    if not input_value:
        return (set(), False, False)
    latest_index = len(input_value) - 1
    latest_mapping = _json_mapping_or_none(input_value[latest_index])
    latest_type = latest_mapping.get("type") if latest_mapping is not None else None
    terminal_trigger_indices: set[int] = set()
    if latest_type == "compaction_trigger":
        terminal_trigger_indices.add(latest_index)
        if latest_index == 0:
            return (terminal_trigger_indices, True, False)
        latest_index -= 1
        latest_mapping = _json_mapping_or_none(input_value[latest_index])
        latest_type = latest_mapping.get("type") if latest_mapping is not None else None

    def with_terminal_trigger(indices: set[int]) -> set[int]:
        return indices | terminal_trigger_indices

    if latest_type not in _COMPACT_TOOL_CALL_ITEM_TYPES | _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES:
        return (with_terminal_trigger({latest_index}), True, False)
    if latest_mapping is not None and _compact_item_is_state_anchor(latest_mapping):
        terminal_indices = _compact_required_terminal_indices(input_value, latest_index, token_counts)
        return (with_terminal_trigger(terminal_indices), True, True)
    if latest_mapping is not None and _compact_item_has_elidable_inline_image(latest_mapping):
        terminal_indices = _compact_required_terminal_indices(input_value, latest_index, token_counts)
        return (with_terminal_trigger(terminal_indices), True, True)
    matching_call_index = _compact_matching_tool_call_index(input_value, latest_index)
    if latest_mapping is not None and _compact_terminal_item_is_side_effect(
        input_value, latest_index, matching_call_index=matching_call_index
    ):
        terminal_indices = _compact_required_terminal_indices(input_value, latest_index, token_counts)
        return (with_terminal_trigger(terminal_indices), True, True)
    if latest_type in _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES and has_continuity_anchor and (matching_call_index is None):
        return (with_terminal_trigger({latest_index}), True, False)
    if latest_type in _COMPACT_TOOL_CALL_ITEM_TYPES:
        terminal_indices = _compact_required_terminal_indices(input_value, latest_index, token_counts)
        return (with_terminal_trigger(terminal_indices), True, True)
    if terminal_trigger_indices:
        return (terminal_trigger_indices, True, False)
    paired_tail = _compact_reconciled_tool_call_indices(
        input_value,
        with_terminal_trigger({latest_index}),
        token_counts=token_counts,
        token_budget=_MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS,
    )
    if latest_index in paired_tail:
        return (paired_tail, False, False)
    return (terminal_trigger_indices, bool(terminal_trigger_indices), False)


def _compact_item_has_elidable_inline_image(item: JsonValue) -> bool:
    _, changed = _compact_elide_inline_images(item)
    return changed


def _compact_terminal_item_is_side_effect(
    input_value: list[JsonValue], index: int, *, matching_call_index: int | None
) -> bool:
    item = _json_mapping_or_none(input_value[index])
    if item is None:
        return False
    if item.get("type") in _COMPACT_SIDE_EFFECT_TOOL_ITEM_TYPES or _compact_item_is_side_effect_anchor(item):
        return True
    if matching_call_index is None:
        return False
    matching_call = _json_mapping_or_none(input_value[matching_call_index])
    return matching_call is not None and (
        matching_call.get("type") in _COMPACT_SIDE_EFFECT_TOOL_ITEM_TYPES
        or _compact_item_is_side_effect_anchor(matching_call)
    )


def _compact_required_terminal_indices(
    input_value: list[JsonValue], latest_index: int, token_counts: list[int]
) -> set[int]:
    return _compact_reconciled_tool_call_indices(
        input_value,
        {latest_index},
        token_counts=token_counts,
        token_budget=sum(token_counts),
        required_indices={latest_index},
    )


def _compact_state_anchor_indices(input_value: list[JsonValue]) -> set[int]:
    preserved_indices: set[int] = set()
    for index, item in enumerate(input_value):
        if not is_json_mapping(item):
            continue
        item_mapping = item
        if item_mapping.get("type") == "additional_tools":
            preserved_indices.add(index)
            developer_index = index + 1
            if developer_index < len(input_value):
                developer_item = input_value[developer_index]
                if is_json_mapping(developer_item) and developer_item.get("role") == "developer":
                    developer_type = developer_item.get("type")
                    if developer_type is None or developer_type == "message":
                        preserved_indices.add(developer_index)
        if _is_preserved_non_message_directive(item_mapping):
            preserved_indices.add(index)
        if _compact_item_is_state_anchor(item_mapping):
            preserved_indices.add(index)
    return preserved_indices


def _compact_item_is_state_anchor(item: Mapping[str, JsonValue]) -> bool:
    item_type = item.get("type")
    if item_type == "additional_tools":
        return True
    if item_type in {None, "message"} and item.get("role") in {"system", "developer"}:
        return True
    if item_type == "function_call":
        name = item.get("name")
        if isinstance(name, str) and name in _COMPACT_STATE_TOOL_NAMES:
            return True
        function = item.get("function")
        if is_json_mapping(function):
            function_name = function.get("name")
            if isinstance(function_name, str) and function_name in _COMPACT_STATE_TOOL_NAMES:
                return True
    for text in _compact_item_texts(item):
        stripped = text.lstrip()
        if stripped.startswith(_GOAL_CONTINUATION_CONTEXT_PREFIX):
            return True
        if stripped.startswith(_PLAN_MODE_CONTEXT_PREFIX):
            return True
    return False


def _compact_trim_prefix_count(token_counts: list[int]) -> int:
    used = 0
    count = 0
    for token_count in token_counts:
        if used + token_count > _COMPACT_UPSTREAM_HEAD_ESTIMATED_TOKENS:
            break
        used += token_count
        count += 1
    return count


def _compact_trim_suffix_indices(
    token_counts: list[int], *, selected_indices: set[int], start_index: int, token_budget: int
) -> set[int]:
    used = 0
    indices: set[int] = set()
    for index in range(len(token_counts) - 1, start_index - 1, -1):
        if index in selected_indices:
            continue
        token_count = token_counts[index]
        if indices and used + token_count > token_budget:
            break
        if not indices and token_count > token_budget:
            indices.add(index)
            break
        used += token_count
        indices.add(index)
    return indices
