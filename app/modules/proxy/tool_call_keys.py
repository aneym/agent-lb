from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from app.core.types import JsonValue

_PARALLEL_TOOL_CALL_NAME = "multi_tool_use.parallel"


_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
    {
        "apply_patch",
        "close_agent",
        "create_goal",
        "exec_command",
        "request_user_input",
        "resume_agent",
        "send_input",
        "spawn_agent",
        "update_goal",
        "update_plan",
        "wait_agent",
        "write_stdin",
    }
)


_SIDE_EFFECT_TOOL_CALL_NAMES = frozenset(
    {
        _PARALLEL_TOOL_CALL_NAME,
        *_DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES,
        *(f"functions.{name}" for name in _DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
    }
)


_SIDE_EFFECT_TOOL_CALL_ITEM_TYPES = frozenset({"apply_patch_call"})


_PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES = frozenset(
    {
        *(f"functions.{name}" for name in _DIRECT_SIDE_EFFECT_TOOL_CALL_NAMES),
        "multi_tool_use.parallel",
    }
)


_SIDE_EFFECT_VOLATILE_ARG_KEYS = frozenset({"max_output_tokens", "timeout_ms", "yield_time_ms"})


def json_object_from_argument(argument_value: str) -> dict[str, JsonValue] | None:
    try:
        decoded_argument = json.loads(argument_value)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded_argument, dict):
        return None
    return cast(dict[str, JsonValue], decoded_argument)


def canonical_json_key(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_parameters_key(recipient_name: str, parameters: dict[str, JsonValue]) -> str:
    canonical_parameters = dict(parameters)
    for key in _SIDE_EFFECT_VOLATILE_ARG_KEYS:
        canonical_parameters.pop(key, None)
    return canonical_json_key({"recipient_name": recipient_name, "parameters": canonical_parameters})


def canonical_wait_agent_targets(targets: JsonValue | None) -> JsonValue | None:
    if not isinstance(targets, list):
        return targets
    return cast(
        JsonValue,
        sorted(
            targets,
            key=lambda target: (
                target.__class__.__name__,
                canonical_json_key(cast(JsonValue, target)),
            ),
        ),
    )


def canonical_side_effect_argument_key(item_name: str | None, argument_value: str) -> str:
    argument = json_object_from_argument(argument_value)
    if argument is None:
        return argument_value
    normalized_item_name = normalize_tool_call_name(item_name)
    if normalized_item_name == "write_stdin":
        return canonical_json_key(
            {
                "name": normalized_item_name,
                "session_id": argument.get("session_id"),
                "chars": argument.get("chars"),
                "yield_time_ms": argument.get("yield_time_ms"),
            }
        )
    if normalized_item_name == "wait_agent":
        return canonical_json_key(
            {
                "name": normalized_item_name,
                "targets": canonical_wait_agent_targets(argument.get("targets")),
            }
        )
    if normalized_item_name == "exec_command":
        return canonical_parameters_key(normalized_item_name, argument)
    if item_name != _PARALLEL_TOOL_CALL_NAME:
        return canonical_json_key({"name": normalized_item_name or item_name, "parameters": cast(JsonValue, argument)})

    tool_uses = argument.get("tool_uses")
    if not isinstance(tool_uses, list):
        return canonical_json_key(cast(JsonValue, argument))

    canonical_tool_uses: list[JsonValue] = []
    for tool_use in tool_uses:
        if isinstance(tool_use, dict):
            canonical_tool_use = json_object_from_argument(canonical_parallel_tool_use_key(tool_use))
            canonical_tool_uses.append(canonical_tool_use or tool_use)
        else:
            canonical_tool_uses.append(cast(JsonValue, tool_use))
    canonical_argument = dict(argument)
    canonical_argument["tool_uses"] = canonical_tool_uses
    return canonical_json_key(cast(JsonValue, canonical_argument))


def canonical_downstream_side_effect_argument_key(item_name: str | None, argument_value: str) -> str:
    argument = json_object_from_argument(argument_value)
    if argument is None:
        return argument_value
    normalized_item_name = normalize_tool_call_name(item_name)
    if normalized_item_name == "write_stdin":
        return canonical_parameters_key(
            normalized_item_name,
            {
                "session_id": argument.get("session_id"),
                "chars": argument.get("chars"),
                "yield_time_ms": argument.get("yield_time_ms"),
                "max_output_tokens": argument.get("max_output_tokens"),
            },
        )
    if normalized_item_name == "exec_command":
        return canonical_parameters_key(normalized_item_name, argument)
    return canonical_side_effect_argument_key(item_name, argument_value)


def _tool_call_has_side_effect_arguments(item_name: str | None, argument_value: str) -> bool:
    if item_name != _PARALLEL_TOOL_CALL_NAME:
        return item_name in _SIDE_EFFECT_TOOL_CALL_NAMES

    argument = json_object_from_argument(argument_value)
    if argument is None:
        return False
    tool_uses = argument.get("tool_uses")
    if not isinstance(tool_uses, list):
        return False
    for tool_use in tool_uses:
        if not isinstance(tool_use, dict):
            continue
        recipient_name = tool_use.get("recipient_name")
        if isinstance(recipient_name, str) and recipient_name in _PARALLEL_TOOL_USE_DEDUPE_RECIPIENT_NAMES:
            return True
    return False


def canonical_parallel_tool_use_key(tool_use: Mapping[str, JsonValue]) -> str:
    recipient_name = tool_use.get("recipient_name")
    parameters = tool_use.get("parameters")
    if not isinstance(recipient_name, str):
        return json.dumps(tool_use, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    normalized_recipient_name = normalize_tool_call_name(recipient_name)
    if normalized_recipient_name == "write_stdin" and isinstance(parameters, dict):
        return canonical_parameters_key(
            normalized_recipient_name,
            {
                "session_id": parameters.get("session_id"),
                "chars": parameters.get("chars"),
                "yield_time_ms": parameters.get("yield_time_ms"),
                "max_output_tokens": parameters.get("max_output_tokens"),
            },
        )
    if normalized_recipient_name == "wait_agent" and isinstance(parameters, dict):
        targets = parameters.get("targets")
        return json.dumps(
            {
                "recipient_name": normalized_recipient_name,
                "targets": canonical_wait_agent_targets(targets),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    if normalized_recipient_name == "exec_command" and isinstance(parameters, dict):
        return canonical_parameters_key(normalized_recipient_name, cast(dict[str, JsonValue], parameters))
    return json.dumps(tool_use, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalize_tool_call_name(name: str | None) -> str | None:
    if name is None:
        return None
    if name.startswith("functions."):
        return name.removeprefix("functions.")
    return name
