from __future__ import annotations

import json
import re
from collections.abc import Mapping

from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.request_input import _json_list_or_none, _json_mapping_or_none, _sanitize_input_items
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping

type MutableJsonObject = dict[str, JsonValue]
_COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE: dict[str, str] = {
    "function_call_output": "function_call",
    "custom_tool_call_output": "custom_tool_call",
    "apply_patch_call_output": "apply_patch_call",
}
_COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES = frozenset(_COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE)
_TOOL_CALL_ITEM_TYPES = frozenset(_COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE.values())
_COMPACT_INLINE_IMAGE_DATA_URL_RE = re.compile("data:image/[^,\\s]+,[^\\s\"'<>]+")
_GOAL_CONTINUATION_CONTEXT_PREFIX = '<codex_internal_context source="goal">'
_EXPLICIT_PROMPT_CACHE_CONTENT_TYPES = frozenset({"input_text", "input_image", "input_file"})
_UNSUPPORTED_UPSTREAM_FIELDS = {
    "max_output_tokens",
    "metadata",
    "prompt_cache_retention",
    "safety_identifier",
    "temperature",
    "top_p",
    "truncation",
    "user",
}


def _strip_subscription_prompt_cache_controls(payload: MutableJsonObject) -> None:
    payload.pop("prompt_cache_options", None)
    _strip_subscription_prompt_cache_breakpoints(payload.get("input"))


def _strip_subscription_prompt_cache_breakpoints(value: JsonValue | None) -> None:
    if isinstance(value, list):
        for item in value:
            _strip_subscription_prompt_cache_breakpoints(item)
        return
    if not isinstance(value, dict):
        return
    value_type = value.get("type")
    if isinstance(value_type, str) and value_type in _EXPLICIT_PROMPT_CACHE_CONTENT_TYPES:
        value.pop("prompt_cache_breakpoint", None)
    for child in value.values():
        _strip_subscription_prompt_cache_breakpoints(child)


def strip_replayed_tool_call_namespaces_from_payload(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    if not is_json_list(input_value):
        return
    normalized_items: list[JsonValue] = []
    changed = False
    for item in input_value:
        if not is_json_mapping(item):
            normalized_items.append(item)
            continue
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type in _TOOL_CALL_ITEM_TYPES and ("namespace" in item):
            normalized_item = dict(item)
            normalized_item.pop("namespace")
            normalized_items.append(normalized_item)
            changed = True
        else:
            normalized_items.append(item)
    if changed:
        payload["input"] = normalized_items


_POISONED_LOCAL_COMPACT_FALLBACK_TEXT = "Local compact fallback preserved the latest encrypted reasoning state."
_MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS = 100000
_ESTIMATED_CHARS_PER_TOKEN = 4
_COMPACT_OMITTED_INLINE_IMAGE_TEXT = (
    "[compact trim] Omitted inline image bytes that were already observed before compaction"
)


def _strip_unsupported_fields(
    payload: MutableJsonObject, *, strip_replayed_tool_call_namespaces: bool = True
) -> MutableJsonObject:
    _normalize_openai_compatible_aliases(payload)
    _normalize_service_tier_aliases(payload)
    _strip_subscription_prompt_cache_controls(payload)
    _sanitize_interleaved_reasoning_input(payload)
    _strip_poisoned_local_compact_fallback_items(payload)
    if strip_replayed_tool_call_namespaces:
        strip_replayed_tool_call_namespaces_from_payload(payload)
    for key in _UNSUPPORTED_UPSTREAM_FIELDS:
        payload.pop(key, None)
    return payload


def _strip_poisoned_local_compact_fallback_items(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    if not is_json_list(input_value):
        return
    input_items = input_value
    kept: list[JsonValue] = []
    skip_next_poison_compaction = False
    changed = False
    for item in input_items:
        if skip_next_poison_compaction and is_json_mapping(item) and (item.get("type") == "compaction"):
            encrypted_content = item.get("encrypted_content")
            if isinstance(encrypted_content, str) and encrypted_content:
                skip_next_poison_compaction = False
                changed = True
                continue
        skip_next_poison_compaction = False
        if _is_poisoned_local_compact_fallback_message(item):
            skip_next_poison_compaction = True
            changed = True
            continue
        kept.append(item)
    if changed:
        payload["input"] = kept


def _is_poisoned_local_compact_fallback_message(item: JsonValue) -> bool:
    if not is_json_mapping(item):
        return False
    if item.get("type") != "message" or item.get("role") != "assistant":
        return False
    content = item.get("content")
    if not is_json_list(content):
        return False
    for part in content:
        if not is_json_mapping(part):
            continue
        if part.get("text") == _POISONED_LOCAL_COMPACT_FALLBACK_TEXT:
            return True
    return False


def validate_compact_input_wire_budget(payload: Mapping[str, JsonValue]) -> None:
    input_value = payload.get("input")
    if not is_json_list(input_value):
        return
    if _estimated_json_tokens(input_value) <= _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS:
        return
    raise ClientPayloadError(
        "Compact input exceeds the upstream size limit after preparing the final wire payload.",
        param="input",
        code="responses_compact_input_too_large",
        error_type="invalid_request_error",
    )


def _compact_item_texts(item: Mapping[str, JsonValue]) -> list[str]:
    content = item.get("content")
    if isinstance(content, str):
        return [content]
    if is_json_mapping(content):
        content_parts: list[JsonValue] = [content]
    elif is_json_list(content):
        content_parts = content
    else:
        return []
    texts: list[str] = []
    for part in content_parts:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not is_json_mapping(part):
            continue
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
    return texts


def responses_input_contains_goal_continuation_context(input_value: JsonValue) -> bool:
    if not is_json_list(input_value):
        return False
    for item in input_value:
        if not is_json_mapping(item):
            continue
        for text in _compact_item_texts(item):
            if text.lstrip().startswith(_GOAL_CONTINUATION_CONTEXT_PREFIX):
                return True
    return False


def _estimated_json_tokens(value: JsonValue) -> int:
    serialized = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return max(1, (len(serialized) + _ESTIMATED_CHARS_PER_TOKEN - 1) // _ESTIMATED_CHARS_PER_TOKEN)


def _sanitize_interleaved_reasoning_input(payload: MutableJsonObject) -> None:
    input_value = payload.get("input")
    input_items = _json_list_or_none(input_value)
    if input_items is None:
        return
    payload["input"] = _sanitize_input_items(input_items)


def normalize_reasoning_aliases(payload: MutableJsonObject) -> None:
    reasoning_effort = payload.pop("reasoningEffort", None)
    snake_case_reasoning_effort = payload.pop("reasoning_effort", None)
    reasoning_summary = payload.pop("reasoningSummary", None)
    provider_thinking = payload.pop("thinking", None)
    provider_enable_thinking = payload.pop("enable_thinking", None)
    reasoning_payload = _json_mapping_or_none(payload.get("reasoning"))
    if reasoning_payload is not None:
        reasoning_map: MutableJsonObject = dict(reasoning_payload.items())
    else:
        reasoning_map = {}
    existing_effort = reasoning_map.get("effort")
    if isinstance(existing_effort, str) and (not existing_effort.strip()):
        reasoning_map.pop("effort")
    alias_effort = next(
        (
            candidate.strip()
            for candidate in (reasoning_effort, snake_case_reasoning_effort)
            if isinstance(candidate, str) and candidate.strip()
        ),
        None,
    )
    if alias_effort is not None and "effort" not in reasoning_map:
        reasoning_map["effort"] = alias_effort
    if isinstance(reasoning_summary, str) and "summary" not in reasoning_map:
        reasoning_map["summary"] = reasoning_summary
    provider_reasoning = _normalize_thinking_alias(provider_thinking, enable_thinking=provider_enable_thinking)
    if provider_reasoning is not None:
        if "effort" not in reasoning_map and "effort" in provider_reasoning:
            reasoning_map["effort"] = provider_reasoning["effort"]
        if "summary" not in reasoning_map and "summary" in provider_reasoning:
            reasoning_map["summary"] = provider_reasoning["summary"]
    if reasoning_map:
        payload["reasoning"] = reasoning_map


def _normalize_thinking_alias(thinking: JsonValue, *, enable_thinking: JsonValue) -> MutableJsonObject | None:
    if isinstance(thinking, bool):
        if thinking:
            return {"effort": "medium"}
    if isinstance(thinking, str):
        normalized = thinking.strip().lower()
        if normalized in {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            return {"effort": normalized}
        if normalized in {"enabled", "true", "on"}:
            return {"effort": "medium"}
    thinking_mapping = _json_mapping_or_none(thinking)
    if thinking_mapping is not None:
        normalized: MutableJsonObject = {}
        effort = thinking_mapping.get("effort")
        summary = thinking_mapping.get("summary")
        if isinstance(effort, str) and effort.strip():
            normalized["effort"] = effort.strip().lower()
        if isinstance(summary, str) and summary.strip():
            normalized["summary"] = summary.strip()
        thinking_type = thinking_mapping.get("type")
        if (
            "effort" not in normalized
            and isinstance(thinking_type, str)
            and (thinking_type.strip().lower() == "enabled")
        ):
            normalized["effort"] = "medium"
        enabled = thinking_mapping.get("enabled")
        if "effort" not in normalized and enabled is True:
            normalized["effort"] = "medium"
        if "effort" not in normalized and enable_thinking is True:
            normalized["effort"] = "medium"
        if normalized:
            return normalized
    if isinstance(enable_thinking, bool):
        return {"effort": "medium"} if enable_thinking else None
    return None


def _normalize_openai_compatible_aliases(payload: MutableJsonObject) -> None:
    text_verbosity = payload.pop("textVerbosity", None)
    top_level_verbosity = payload.pop("verbosity", None)
    prompt_cache_key = payload.pop("promptCacheKey", None)
    prompt_cache_retention = payload.pop("promptCacheRetention", None)
    if isinstance(prompt_cache_key, str) and "prompt_cache_key" not in payload:
        payload["prompt_cache_key"] = prompt_cache_key
    if isinstance(prompt_cache_retention, str) and "prompt_cache_retention" not in payload:
        payload["prompt_cache_retention"] = prompt_cache_retention
    normalize_reasoning_aliases(payload)
    text_payload = _json_mapping_or_none(payload.get("text"))
    if text_payload is not None:
        text_map: MutableJsonObject = dict(text_payload.items())
    else:
        text_map = {}
    if isinstance(text_verbosity, str) and "verbosity" not in text_map:
        text_map["verbosity"] = text_verbosity
    if isinstance(top_level_verbosity, str) and "verbosity" not in text_map:
        text_map["verbosity"] = top_level_verbosity
    if text_map:
        payload["text"] = text_map


def _normalize_service_tier_aliases(payload: MutableJsonObject) -> None:
    service_tier = payload.get("service_tier")
    normalized = _normalize_service_tier_alias_value(service_tier)
    if isinstance(normalized, str):
        payload["service_tier"] = normalized


def _normalize_service_tier_alias_value(value: JsonValue) -> JsonValue:
    if not isinstance(value, str):
        return value
    if value.strip().lower() == "fast":
        return "priority"
    return value
