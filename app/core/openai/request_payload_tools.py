from __future__ import annotations

from app.core.openai.request_compatibility import UNSUPPORTED_TOOL_TYPES, normalize_tool_type
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping

_RESPONSES_INCLUDE_ALLOWLIST = {
    "code_interpreter_call.outputs",
    "computer_call_output.output.image_url",
    "file_search_call.results",
    "message.input_image.image_url",
    "message.output_text.logprobs",
    "reasoning.encrypted_content",
    "web_search_call.action.sources",
}


def normalize_tool_choice(choice: JsonValue | None) -> JsonValue | None:
    if not is_json_mapping(choice):
        return choice
    choice_mapping = choice
    tool_type = choice_mapping.get("type")
    if isinstance(tool_type, str):
        normalized_type = normalize_tool_type(tool_type)
        if normalized_type != tool_type:
            updated = dict(choice_mapping)
            updated["type"] = normalized_type
            return updated
    return choice


def validate_tool_types(tools: list[JsonValue], *, allow_builtin_tools: bool = False) -> list[JsonValue]:
    normalized_tools: list[JsonValue] = []
    for tool in tools:
        if not is_json_mapping(tool):
            normalized_tools.append(tool)
            continue
        tool_mapping = tool
        tool_type = tool_mapping.get("type")
        if isinstance(tool_type, str):
            normalized_type = normalize_tool_type(tool_type)
            if normalized_type != tool_type:
                tool = dict(tool_mapping)
                tool["type"] = normalized_type
                tool_type = normalized_type
            if not allow_builtin_tools and tool_type in UNSUPPORTED_TOOL_TYPES:
                raise ValueError(f"Unsupported tool type: {tool_type}")
        normalized_tools.append(tool)
    return normalized_tools
