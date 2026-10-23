from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from app.core.eval_guard import strip_eval_web_tools
from app.core.openai.compact_budget import _COMPACT_SIDE_EFFECT_TOOL_ITEM_TYPES as _COMPACT_SIDE_EFFECT_TOOL_ITEM_TYPES
from app.core.openai.compact_budget import _COMPACT_STATE_TOOL_NAMES as _COMPACT_STATE_TOOL_NAMES
from app.core.openai.compact_budget import (
    _COMPACT_UPSTREAM_HEAD_ESTIMATED_TOKENS as _COMPACT_UPSTREAM_HEAD_ESTIMATED_TOKENS,
)
from app.core.openai.compact_budget import _PLAN_MODE_CONTEXT_PREFIX as _PLAN_MODE_CONTEXT_PREFIX
from app.core.openai.compact_budget import (
    _compact_discard_consumed_continuity_output_pairs as _compact_discard_consumed_continuity_output_pairs,
)
from app.core.openai.compact_budget import (
    _compact_item_has_elidable_inline_image as _compact_item_has_elidable_inline_image,
)
from app.core.openai.compact_budget import _compact_item_is_state_anchor as _compact_item_is_state_anchor
from app.core.openai.compact_budget import _compact_required_indices as _compact_required_indices
from app.core.openai.compact_budget import _compact_required_terminal_indices as _compact_required_terminal_indices
from app.core.openai.compact_budget import _compact_state_anchor_indices as _compact_state_anchor_indices
from app.core.openai.compact_budget import (
    _compact_terminal_item_is_side_effect as _compact_terminal_item_is_side_effect,
)
from app.core.openai.compact_budget import _compact_terminal_required_indices as _compact_terminal_required_indices
from app.core.openai.compact_budget import _compact_trim_prefix_count as _compact_trim_prefix_count
from app.core.openai.compact_budget import _compact_trim_suffix_indices as _compact_trim_suffix_indices
from app.core.openai.compact_budget import _is_preserved_non_message_directive as _is_preserved_non_message_directive
from app.core.openai.compact_context import _COMPACT_TOOL_CALL_ITEM_TYPES as _COMPACT_TOOL_CALL_ITEM_TYPES
from app.core.openai.compact_context import _compact_elide_inline_images as _compact_elide_inline_images
from app.core.openai.compact_context import (
    _compact_elide_required_tool_output_images as _compact_elide_required_tool_output_images,
)
from app.core.openai.compact_context import (
    _compact_fit_selected_indices_to_wire_budget as _compact_fit_selected_indices_to_wire_budget,
)
from app.core.openai.compact_context import _compact_has_continuity_anchor as _compact_has_continuity_anchor
from app.core.openai.compact_context import _compact_item_is_side_effect_anchor as _compact_item_is_side_effect_anchor
from app.core.openai.compact_context import _compact_matching_tool_call_index as _compact_matching_tool_call_index
from app.core.openai.compact_context import (
    _compact_reconciled_tool_call_indices as _compact_reconciled_tool_call_indices,
)
from app.core.openai.compact_context import _compact_side_effect_anchor_indices as _compact_side_effect_anchor_indices
from app.core.openai.compact_context import _compact_trim_marker as _compact_trim_marker
from app.core.openai.compact_context import _compact_trimmed_input_with_markers as _compact_trimmed_input_with_markers
from app.core.openai.compact_context import _estimated_json_array_item_tokens as _estimated_json_array_item_tokens
from app.core.openai.request_compatibility import _TOOL_TYPE_ALIASES as _TOOL_TYPE_ALIASES
from app.core.openai.request_compatibility import UNSUPPORTED_TOOL_TYPES as UNSUPPORTED_TOOL_TYPES
from app.core.openai.request_compatibility import _canonicalize_tools as _canonicalize_tools
from app.core.openai.request_compatibility import _compact_losslessly_trim_input as _compact_losslessly_trim_input
from app.core.openai.request_compatibility import (
    _contains_explicit_prompt_cache_breakpoint as _contains_explicit_prompt_cache_breakpoint,
)
from app.core.openai.request_compatibility import _is_responses_lite_input as _is_responses_lite_input
from app.core.openai.request_compatibility import _json_parts as _json_parts
from app.core.openai.request_compatibility import _merge_responses_instructions as _merge_responses_instructions
from app.core.openai.request_compatibility import (
    _normalize_compaction_trigger_singleton as _normalize_compaction_trigger_singleton,
)
from app.core.openai.request_compatibility import (
    _normalize_responses_input_instructions as _normalize_responses_input_instructions,
)
from app.core.openai.request_compatibility import (
    _responses_instruction_content_text as _responses_instruction_content_text,
)
from app.core.openai.request_compatibility import _responses_instruction_item_text as _responses_instruction_item_text
from app.core.openai.request_compatibility import _sort_keys_recursive as _sort_keys_recursive
from app.core.openai.request_compatibility import (
    _split_responses_instruction_item_content as _split_responses_instruction_item_content,
)
from app.core.openai.request_compatibility import _strip_compact_unsupported_fields as _strip_compact_unsupported_fields
from app.core.openai.request_compatibility import _tool_sort_key as _tool_sort_key
from app.core.openai.request_compatibility import _trim_compact_input_for_upstream as _trim_compact_input_for_upstream
from app.core.openai.request_compatibility import canonicalized_tools as canonicalized_tools
from app.core.openai.request_compatibility import normalize_tool_type as normalize_tool_type
from app.core.openai.request_compatibility import responses_input_uses_lite_tools as responses_input_uses_lite_tools
from app.core.openai.request_input import (
    InputImageFileReference as InputImageFileReference,
)
from app.core.openai.request_input import _normalize_input_text, _sanitize_input_items
from app.core.openai.request_input import (
    extract_input_file_ids as extract_input_file_ids,
)
from app.core.openai.request_input import (
    extract_input_image_file_references as extract_input_image_file_references,
)
from app.core.openai.request_payload_tools import _RESPONSES_INCLUDE_ALLOWLIST as _RESPONSES_INCLUDE_ALLOWLIST
from app.core.openai.request_payload_tools import normalize_tool_choice as normalize_tool_choice
from app.core.openai.request_payload_tools import validate_tool_types as validate_tool_types
from app.core.openai.request_payload_values import (
    _COMPACT_INLINE_IMAGE_DATA_URL_RE as _COMPACT_INLINE_IMAGE_DATA_URL_RE,
)
from app.core.openai.request_payload_values import (
    _COMPACT_OMITTED_INLINE_IMAGE_TEXT as _COMPACT_OMITTED_INLINE_IMAGE_TEXT,
)
from app.core.openai.request_payload_values import (
    _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES as _COMPACT_TOOL_CALL_OUTPUT_ITEM_TYPES,
)
from app.core.openai.request_payload_values import (
    _COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE as _COMPACT_TOOL_CALL_TYPE_BY_OUTPUT_TYPE,
)
from app.core.openai.request_payload_values import _ESTIMATED_CHARS_PER_TOKEN as _ESTIMATED_CHARS_PER_TOKEN
from app.core.openai.request_payload_values import (
    _EXPLICIT_PROMPT_CACHE_CONTENT_TYPES as _EXPLICIT_PROMPT_CACHE_CONTENT_TYPES,
)
from app.core.openai.request_payload_values import (
    _GOAL_CONTINUATION_CONTEXT_PREFIX as _GOAL_CONTINUATION_CONTEXT_PREFIX,
)
from app.core.openai.request_payload_values import (
    _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS as _MAX_COMPACT_UPSTREAM_ESTIMATED_TOKENS,
)
from app.core.openai.request_payload_values import (
    _POISONED_LOCAL_COMPACT_FALLBACK_TEXT as _POISONED_LOCAL_COMPACT_FALLBACK_TEXT,
)
from app.core.openai.request_payload_values import _TOOL_CALL_ITEM_TYPES as _TOOL_CALL_ITEM_TYPES
from app.core.openai.request_payload_values import _UNSUPPORTED_UPSTREAM_FIELDS as _UNSUPPORTED_UPSTREAM_FIELDS
from app.core.openai.request_payload_values import MutableJsonObject as MutableJsonObject
from app.core.openai.request_payload_values import _compact_item_texts as _compact_item_texts
from app.core.openai.request_payload_values import _estimated_json_tokens as _estimated_json_tokens
from app.core.openai.request_payload_values import (
    _is_poisoned_local_compact_fallback_message as _is_poisoned_local_compact_fallback_message,
)
from app.core.openai.request_payload_values import (
    _normalize_openai_compatible_aliases as _normalize_openai_compatible_aliases,
)
from app.core.openai.request_payload_values import (
    _normalize_service_tier_alias_value as _normalize_service_tier_alias_value,
)
from app.core.openai.request_payload_values import _normalize_service_tier_aliases as _normalize_service_tier_aliases
from app.core.openai.request_payload_values import _normalize_thinking_alias as _normalize_thinking_alias
from app.core.openai.request_payload_values import (
    _sanitize_interleaved_reasoning_input as _sanitize_interleaved_reasoning_input,
)
from app.core.openai.request_payload_values import (
    _strip_poisoned_local_compact_fallback_items as _strip_poisoned_local_compact_fallback_items,
)
from app.core.openai.request_payload_values import (
    _strip_subscription_prompt_cache_breakpoints as _strip_subscription_prompt_cache_breakpoints,
)
from app.core.openai.request_payload_values import (
    _strip_subscription_prompt_cache_controls as _strip_subscription_prompt_cache_controls,
)
from app.core.openai.request_payload_values import _strip_unsupported_fields as _strip_unsupported_fields
from app.core.openai.request_payload_values import normalize_reasoning_aliases as normalize_reasoning_aliases
from app.core.openai.request_payload_values import (
    responses_input_contains_goal_continuation_context as responses_input_contains_goal_continuation_context,
)
from app.core.openai.request_payload_values import (
    strip_replayed_tool_call_namespaces_from_payload as strip_replayed_tool_call_namespaces_from_payload,
)
from app.core.openai.request_payload_values import (
    validate_compact_input_wire_budget as validate_compact_input_wire_budget,
)
from app.core.types import JsonObject, JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping


class ResponsesReasoning(BaseModel):
    model_config = ConfigDict(extra="allow")
    effort: str | None = None
    summary: str | None = None


class ResponsesTextFormat(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)
    type: str | None = None
    strict: bool | None = None
    schema_: JsonValue | None = Field(default=None, alias="schema")
    name: str | None = None


class ResponsesTextControls(BaseModel):
    model_config = ConfigDict(extra="allow")
    verbosity: str | None = None
    format: ResponsesTextFormat | None = None


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    _codex_lb_client_reasoning_effort: str | None = PrivateAttr(default=None)
    _codex_lb_provider_reasoning_effort_materialized: bool = PrivateAttr(default=False)

    @model_validator(mode="before")
    @classmethod
    def _move_input_instruction_messages(cls, data: JsonValue) -> JsonValue:
        return _normalize_responses_input_instructions(data)

    model: str = Field(min_length=1)
    instructions: str
    input: JsonValue
    tools: list[JsonValue] = Field(default_factory=list)
    tool_choice: str | JsonObject | None = None
    parallel_tool_calls: bool | None = None
    reasoning: ResponsesReasoning | None = None
    store: bool = False
    stream: bool | None = None
    include: list[str] = Field(default_factory=list)
    service_tier: str | None = None
    conversation: str | None = None
    previous_response_id: str | None = None
    truncation: str | None = None
    prompt_cache_key: str | None = None
    text: ResponsesTextControls | None = None

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            normalized = _normalize_input_text(value)
            return _sanitize_input_items(normalized)
        if is_json_list(value):
            input_items = value
            return _sanitize_input_items(input_items)
        raise ValueError("input must be a string or array")

    @field_validator("include")
    @classmethod
    def _validate_include(cls, value: list[str]) -> list[str]:
        for entry in value:
            if entry not in _RESPONSES_INCLUDE_ALLOWLIST:
                raise ValueError(f"Unsupported include value: {entry}")
        return value

    @field_validator("truncation")
    @classmethod
    def _validate_truncation(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in {"auto", "disabled"}:
            raise ValueError("truncation must be 'auto' or 'disabled'")
        return value

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool | None) -> bool:
        return False

    @field_validator("previous_response_id")
    @classmethod
    def _normalize_previous_response_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, value: list[JsonValue]) -> list[JsonValue]:
        return validate_tool_types(value, allow_builtin_tools=True)

    @field_validator("tool_choice")
    @classmethod
    def _normalize_tool_choice_field(cls, value: JsonValue | None) -> JsonValue | None:
        return normalize_tool_choice(value)

    @field_validator("service_tier")
    @classmethod
    def _normalize_service_tier_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalize_service_tier_alias_value(value)
        return normalized if isinstance(normalized, str) else value

    @model_validator(mode="after")
    def _validate_conversation(self) -> "ResponsesRequest":
        if self.conversation and self.previous_response_id:
            raise ValueError("Provide either 'conversation' or 'previous_response_id', not both.")
        return self

    def model_dump_for_forwarding(self) -> MutableJsonObject:
        payload: MutableJsonObject = self.model_dump(mode="json", exclude_none=True)
        if "tools" not in self.model_fields_set:
            payload.pop("tools", None)
        return payload

    def to_payload(self) -> JsonObject:
        payload = _strip_unsupported_fields(self.model_dump_for_forwarding())
        _normalize_compaction_trigger_singleton(payload)
        return strip_eval_web_tools(payload, "openai")[0]

    def to_replay_safety_payload(self) -> JsonObject:
        return _strip_unsupported_fields(self.model_dump_for_forwarding(), strip_replayed_tool_call_namespaces=False)


class ResponsesCompactRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    _codex_lb_client_reasoning_effort: str | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _move_input_instruction_messages(cls, data: JsonValue) -> JsonValue:
        return _normalize_responses_input_instructions(data)

    model: str = Field(min_length=1)
    instructions: str
    input: JsonValue
    reasoning: ResponsesReasoning | None = None
    store: bool = False
    service_tier: str | None = None
    prompt_cache_key: str | None = None

    @field_validator("input")
    @classmethod
    def _validate_input_type(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            normalized = _normalize_input_text(value)
            return _sanitize_input_items(normalized)
        if is_json_list(value):
            input_items = value
            return _sanitize_input_items(input_items)
        raise ValueError("input must be a string or array")

    @model_validator(mode="before")
    @classmethod
    def _normalize_service_tier_aliases_before_validation(cls, data: JsonValue) -> JsonValue:
        if not is_json_mapping(data):
            return data
        normalized = dict(data)
        service_tier = normalized.get("service_tier")
        normalized_service_tier = _normalize_service_tier_alias_value(service_tier)
        if isinstance(normalized_service_tier, str):
            normalized["service_tier"] = normalized_service_tier
        return normalized

    @field_validator("store")
    @classmethod
    def _ensure_store_false(cls, value: bool) -> bool:
        return False

    def to_payload(self) -> JsonObject:
        payload: MutableJsonObject = self.model_dump(mode="json", exclude_none=True)
        return _strip_compact_unsupported_fields(payload)


def responses_request_has_explicit_prompt_cache_controls(payload: ResponsesRequest) -> bool:
    extra = payload.model_extra
    if isinstance(extra, dict) and "prompt_cache_options" in extra:
        return True
    return _contains_explicit_prompt_cache_breakpoint(payload.input)


def responses_request_contains_goal_continuation_context(payload: ResponsesRequest) -> bool:
    instructions = payload.instructions
    if isinstance(instructions, str) and any(
        (line.lstrip().startswith(_GOAL_CONTINUATION_CONTEXT_PREFIX) for line in instructions.splitlines())
    ):
        return True
    return responses_input_contains_goal_continuation_context(payload.input)
