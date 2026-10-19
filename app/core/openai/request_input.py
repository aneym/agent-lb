from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

type MutableJsonObject = dict[str, JsonValue]

_INTERLEAVED_REASONING_KEYS = frozenset({"reasoning_content", "reasoning_details", "tool_calls", "function_call"})
_INTERLEAVED_REASONING_PART_TYPES = frozenset({"reasoning", "reasoning_content", "reasoning_details"})
_ASSISTANT_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
_TOOL_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text", "refusal"})


def _json_mapping_or_none(value: JsonValue) -> Mapping[str, JsonValue] | None:
    if not is_json_mapping(value):
        return None
    return value


def _json_parts(value: JsonValue) -> list[JsonValue]:
    if is_json_list(value):
        return value
    return [value]


def _is_input_file_with_id(item: Mapping[str, JsonValue]) -> bool:
    if item.get("type") != "input_file":
        return False
    file_id = item.get("file_id")
    return isinstance(file_id, str) and bool(file_id)


@dataclass(frozen=True, slots=True)
class InputImageFileReference:
    item_index: int
    content_index: int | None
    file_id: str


def _input_image_file_reference(item: Mapping[str, JsonValue]) -> str | None:
    if item.get("type") != "input_image":
        return None
    file_id = item.get("file_id")
    if isinstance(file_id, str) and file_id:
        return file_id
    image_url = item.get("image_url")
    if not isinstance(image_url, str) or not image_url.startswith("sediment://"):
        return None
    resolved = image_url.removeprefix("sediment://").strip()
    return resolved or None


def extract_input_file_ids(input_value: JsonValue) -> set[str]:

    if not is_json_list(input_value):
        return set()
    file_ids: set[str] = set()
    for item in input_value:
        if not is_json_mapping(item):
            continue
        item_mapping = item
        if _is_input_file_with_id(item_mapping):
            file_id = item_mapping.get("file_id")
            if isinstance(file_id, str) and file_id:
                file_ids.add(file_id)
        image_file_id = _input_image_file_reference(item_mapping)
        if image_file_id is not None:
            file_ids.add(image_file_id)
        content = item_mapping.get("content")
        if is_json_list(content):
            parts: list[JsonValue] = content
        elif is_json_mapping(content):
            parts = [content]
        else:
            parts = []
        for part in parts:
            if not is_json_mapping(part):
                continue
            if _is_input_file_with_id(part):
                file_id = part.get("file_id")
                if isinstance(file_id, str) and file_id:
                    file_ids.add(file_id)
            image_file_id = _input_image_file_reference(part)
            if image_file_id is not None:
                file_ids.add(image_file_id)
    return file_ids


def _append_input_image_file_references(
    references: list[InputImageFileReference],
    value: JsonValue,
    *,
    item_index: int,
    content_index: int | None,
) -> None:
    if is_json_mapping(value):
        file_id = _input_image_file_reference(value)
        if file_id is not None:
            references.append(
                InputImageFileReference(
                    item_index=item_index,
                    content_index=content_index,
                    file_id=file_id,
                )
            )
        for child in value.values():
            _append_input_image_file_references(
                references,
                child,
                item_index=item_index,
                content_index=content_index,
            )
        return
    if is_json_list(value):
        for child in value:
            _append_input_image_file_references(
                references,
                child,
                item_index=item_index,
                content_index=content_index,
            )


def extract_input_image_file_references(input_value: JsonValue) -> list[InputImageFileReference]:
    if not is_json_list(input_value):
        return []
    references: list[InputImageFileReference] = []
    for item_index, item in enumerate(input_value):
        if not is_json_mapping(item):
            continue
        item_mapping = item
        top_level_file_id = _input_image_file_reference(item_mapping)
        if top_level_file_id is not None:
            references.append(
                InputImageFileReference(
                    item_index=item_index,
                    content_index=None,
                    file_id=top_level_file_id,
                )
            )
        content = item_mapping.get("content")
        if is_json_list(content):
            parts: list[JsonValue] = content
        elif is_json_mapping(content):
            parts = [content]
        else:
            parts = []
        for content_index, part in enumerate(parts):
            _append_input_image_file_references(
                references,
                part,
                item_index=item_index,
                content_index=content_index,
            )
        output = item_mapping.get("output")
        _append_input_image_file_references(
            references,
            output,
            item_index=item_index,
            content_index=None,
        )
    return references


def _sanitize_input_items(input_items: list[JsonValue]) -> list[JsonValue]:
    sanitized_input: list[JsonValue] = []
    for item in input_items:
        sanitized_item = _sanitize_interleaved_reasoning_input_item(item)
        if sanitized_item is None:
            continue
        sanitized_input.append(_normalize_role_input_item(sanitized_item))
    return sanitized_input


def _sanitize_interleaved_reasoning_input_item(item: JsonValue) -> JsonValue | None:
    item_mapping = _json_mapping_or_none(item)
    if item_mapping is None:
        return item

    sanitized_item: MutableJsonObject = {}
    for key, value in item_mapping.items():
        if key in _INTERLEAVED_REASONING_KEYS:
            continue
        if key == "content":
            sanitized_content = _sanitize_interleaved_reasoning_content(value)
            if sanitized_content is None:
                continue
            sanitized_item[key] = sanitized_content
            continue
        sanitized_item[key] = value
    return sanitized_item


def _sanitize_interleaved_reasoning_content(content: JsonValue) -> JsonValue | None:
    if is_json_list(content):
        sanitized_parts: list[JsonValue] = []
        for part in _json_parts(content):
            sanitized_part = _sanitize_interleaved_reasoning_content_part(part)
            if sanitized_part is None:
                continue
            sanitized_parts.append(sanitized_part)
        return sanitized_parts
    content_mapping = _json_mapping_or_none(content)
    if content_mapping is not None:
        return _sanitize_interleaved_reasoning_content_part(content_mapping)
    return content


def _sanitize_interleaved_reasoning_content_part(part: JsonValue) -> JsonValue | None:
    part_mapping = _json_mapping_or_none(part)
    if part_mapping is None:
        return part

    part_type = part_mapping.get("type")
    if isinstance(part_type, str) and part_type in _INTERLEAVED_REASONING_PART_TYPES:
        return None

    sanitized_part = dict(part_mapping)
    for key in _INTERLEAVED_REASONING_KEYS:
        sanitized_part.pop(key, None)
    return sanitized_part


def _normalize_role_input_item(value: JsonValue) -> JsonValue:
    value_mapping = _json_mapping_or_none(value)
    if value_mapping is None:
        return value
    role = value_mapping.get("role")
    if role == "assistant":
        return _normalize_assistant_input_item(value_mapping)
    if role == "tool":
        return _normalize_tool_input_item(value_mapping)
    return value


def _normalize_tool_input_item(value: Mapping[str, JsonValue]) -> JsonValue:
    tool_call_id = value.get("tool_call_id")
    tool_call_id_camel = value.get("toolCallId")
    call_id = value.get("call_id")
    resolved_call_id = tool_call_id if isinstance(tool_call_id, str) and tool_call_id else None
    if resolved_call_id is None and isinstance(tool_call_id_camel, str) and tool_call_id_camel:
        resolved_call_id = tool_call_id_camel
    if resolved_call_id is None and isinstance(call_id, str) and call_id:
        resolved_call_id = call_id
    if not isinstance(resolved_call_id, str) or not resolved_call_id:
        raise ValueError("tool input items must include 'tool_call_id'")
    output = value.get("output")
    output_value = output if output is not None else value.get("content")
    return {
        "type": "function_call_output",
        "call_id": resolved_call_id,
        "output": _normalize_tool_output_value(output_value),
    }


def _normalize_tool_output_value(content: JsonValue) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if is_json_list(content):
        parts: list[str] = []
        for part in _json_parts(content):
            if isinstance(part, str):
                parts.append(part)
                continue
            extracted = _extract_text_content_part(part, _TOOL_TEXT_PART_TYPES)
            if extracted is not None:
                parts.append(extracted)
        if parts:
            return "".join(parts)
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    if is_json_mapping(content):
        extracted = _extract_text_content_part(content, _TOOL_TEXT_PART_TYPES)
        if extracted is not None:
            return extracted
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return str(content)


def _normalize_assistant_input_item(value: Mapping[str, JsonValue]) -> JsonValue:
    content = value.get("content")
    normalized_content = _normalize_assistant_content(content)
    if normalized_content == content:
        return value
    updated = dict(value)
    updated["content"] = normalized_content
    return updated


def _normalize_assistant_content(content: JsonValue) -> JsonValue:
    if content is None:
        return None
    if isinstance(content, str):
        return cast(JsonValue, [{"type": "output_text", "text": content}])
    if is_json_list(content):
        return cast(JsonValue, [_normalize_assistant_content_part(part) for part in _json_parts(content)])
    content_mapping = _json_mapping_or_none(content)
    if content_mapping is not None:
        return [_normalize_assistant_content_part(content_mapping)]
    return content


def _normalize_assistant_content_part(part: JsonValue) -> JsonValue:
    if isinstance(part, str):
        return {"type": "output_text", "text": part}
    if not is_json_mapping(part):
        return part
    text = _extract_text_content_part(part, _ASSISTANT_TEXT_PART_TYPES)
    if text is not None:
        return {"type": "output_text", "text": text}
    return part


def _extract_text_content_part(part: JsonValue, allowed_types: frozenset[str]) -> str | None:
    part_mapping = _json_mapping_or_none(part)
    if part_mapping is None:
        return None
    part_type = part_mapping.get("type")
    text = part_mapping.get("text")
    if ((isinstance(part_type, str) and part_type in allowed_types) or part_type is None) and isinstance(text, str):
        return text
    refusal = part_mapping.get("refusal")
    if isinstance(part_type, str) and part_type == "refusal" and isinstance(refusal, str):
        return refusal
    return None


def _json_list_or_none(value: JsonValue) -> list[JsonValue] | None:
    if not is_json_list(value):
        return None
    return value


def _normalize_input_text(text: str) -> list[JsonValue]:
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]
