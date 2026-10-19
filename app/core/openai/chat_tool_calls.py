from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.core.openai.chat_models import ChatMessageToolCall, ChatToolCallDelta, ChatToolCallFunction
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping


@dataclass
class ToolCallIndex:
    indexes: dict[str, int] = field(default_factory=dict)
    next_index: int = 0
    output_index_map: dict[int, int] = field(default_factory=dict)

    def index_for(self, call_id: str | None, name: str | None) -> int:
        key = _tool_call_key(call_id, name)
        if key is None:
            return 0
        if key not in self.indexes:
            self.indexes[key] = self.next_index
            self.next_index += 1
        return self.indexes[key]

    def index_for_output_index(self, output_index: int | None, call_id: str | None, name: str | None) -> int:

        key = _tool_call_key(call_id, name)
        if output_index is not None and output_index in self.output_index_map:
            idx = self.output_index_map[output_index]
            if key is not None and key not in self.indexes:
                self.indexes[key] = idx
            return idx
        if key is None:
            if output_index is not None:
                idx = self.output_index_map.get(output_index, self.next_index)
                if output_index not in self.output_index_map:
                    self.output_index_map[output_index] = idx
                    self.next_index = max(self.next_index, idx + 1)
                return idx
            return 0
        if key not in self.indexes:
            self.indexes[key] = self.next_index
            self.next_index += 1
        idx = self.indexes[key]
        if output_index is not None and output_index not in self.output_index_map:
            self.output_index_map[output_index] = idx
        return idx

    def register_alias(self, alias_id: str, index: int) -> None:

        key = f"id:{alias_id}"
        if key not in self.indexes:
            self.indexes[key] = index

    def register_output_index(self, output_index: int, index: int) -> None:

        if output_index not in self.output_index_map:
            self.output_index_map[output_index] = index


@dataclass
class ToolCallDelta:
    index: int
    call_id: str | None
    name: str | None
    arguments: str | None
    tool_type: str | None
    arguments_mode: Literal["append", "replace"] = "append"

    def to_chunk_call(self) -> ChatToolCallDelta:
        function = _build_tool_call_function(self.name, self.arguments)
        return ChatToolCallDelta(
            index=self.index,
            id=self.call_id,
            type=self.tool_type or "function",
            function=function,
        )


@dataclass
class ToolCallState:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""
    tool_type: str = "function"
    emitted_call_id: str | None = None
    emitted_name: str | None = None
    emitted_arguments: str = ""
    emitted_tool_type: str | None = None

    def apply_delta(self, delta: ToolCallDelta) -> None:
        if delta.call_id:
            self.call_id = delta.call_id
        if delta.name:
            self.name = delta.name
        if delta.arguments is not None:
            if delta.arguments_mode == "replace":
                self.arguments = delta.arguments
            elif delta.arguments:
                self.arguments += delta.arguments
        if delta.tool_type:
            self.tool_type = delta.tool_type

    def build_stream_delta(self) -> ToolCallDelta | None:
        call_id = _pending_stream_value(self.emitted_call_id, self.call_id)
        name = _pending_stream_value(self.emitted_name, self.name)
        arguments = _pending_stream_arguments(self.emitted_arguments, self.arguments)
        tool_type = self.tool_type or "function"
        if call_id is None and name is None and arguments is None and self.emitted_tool_type == tool_type:
            return None

        if call_id is not None:
            self.emitted_call_id = self.call_id
        if name is not None:
            self.emitted_name = self.name
        if arguments is not None:
            self.emitted_arguments = self.arguments
        self.emitted_tool_type = tool_type
        return ToolCallDelta(
            index=self.index,
            call_id=call_id,
            name=name,
            arguments=arguments,
            tool_type=tool_type,
        )

    def to_message_tool_call(self) -> ChatMessageToolCall | None:
        function = _build_tool_call_function(self.name, self.arguments or None)
        if self.call_id is None and function is None:
            return None
        return ChatMessageToolCall(
            id=self.call_id,
            type=self.tool_type or "function",
            function=function,
        )


def _build_tool_call_function(name: str | None, arguments: str | None) -> ChatToolCallFunction | None:
    if name is None and arguments is None:
        return None
    return ChatToolCallFunction(name=name, arguments=arguments)


def _resolve_output_index(payload: Mapping[str, JsonValue]) -> int | None:

    val = payload.get("output_index")
    if isinstance(val, int):
        return val
    return None


def _register_item_id_alias(payload: Mapping[str, JsonValue], index: int, indexer: ToolCallIndex) -> None:

    item = payload.get("item")
    if not isinstance(item, dict):
        return
    item_id = item.get("id")
    call_id = item.get("call_id")
    if isinstance(item_id, str) and item_id:
        if item_id != call_id:
            indexer.register_alias(item_id, index)

    output_index = _resolve_output_index(payload)
    if output_index is not None:
        indexer.register_output_index(output_index, index)


def _tool_call_delta_from_payload(payload: Mapping[str, JsonValue], indexer: ToolCallIndex) -> ToolCallDelta | None:
    if not _is_tool_call_event(payload):
        return None
    fields = _extract_tool_call_fields(payload)
    if fields is None:
        return None
    call_id, name, arguments, tool_type = fields
    output_index = _resolve_output_index(payload)
    routing_id = call_id
    if routing_id is None:
        item = payload.get("item")
        routing_id = _first_str(
            payload.get("item_id"),
            item.get("id") if isinstance(item, dict) else None,
        )
    index = indexer.index_for_output_index(output_index, routing_id, name)
    _register_item_id_alias(payload, index, indexer)
    return ToolCallDelta(
        index=index,
        call_id=call_id,
        name=name,
        arguments=arguments,
        tool_type=tool_type,
        arguments_mode=_tool_call_arguments_mode(payload),
    )


def _is_tool_call_event(payload: Mapping[str, JsonValue]) -> bool:
    event_type = payload.get("type")
    if isinstance(event_type, str) and ("tool_call" in event_type or "function_call" in event_type):
        return True
    item = _as_mapping(payload.get("item"))
    if item is not None:
        item_type = item.get("type")
        if isinstance(item_type, str) and ("tool" in item_type or "function" in item_type):
            return True
        if any(key in item for key in ("call_id", "tool_call_id", "arguments", "function", "name")):
            return True
    if any(key in payload for key in ("call_id", "tool_call_id")):
        return True
    if "arguments" in payload and ("name" in payload or "function" in payload):
        return True
    return False


def _extract_tool_call_fields(
    payload: Mapping[str, JsonValue],
) -> tuple[str | None, str | None, str | None, str | None] | None:
    candidate = _select_tool_call_candidate(payload)
    delta = candidate.get("delta")
    delta_map = _as_mapping(delta)
    delta_text = delta if isinstance(delta, str) else None

    call_id = _first_str(
        candidate.get("call_id"),
        candidate.get("tool_call_id"),
        candidate.get("id"),
    )
    if call_id is None and delta_map is not None:
        call_id = _first_str(
            delta_map.get("id"),
            delta_map.get("call_id"),
            delta_map.get("tool_call_id"),
        )

    name = _first_str(candidate.get("name"), candidate.get("tool_name"))
    if name is None and delta_map is not None:
        name = _first_str(delta_map.get("name"))
    if name is None:
        function = _as_mapping(candidate.get("function"))
        if function is not None:
            name = _first_str(function.get("name"))
    if name is None and delta_map is not None:
        function = _as_mapping(delta_map.get("function"))
        if function is not None:
            name = _first_str(function.get("name"))

    arguments = None
    candidate_arguments = candidate.get("arguments")
    if isinstance(candidate_arguments, str):
        arguments = candidate_arguments
    if arguments is None and isinstance(delta_text, str):
        arguments = delta_text
    if arguments is None and delta_map is not None:
        delta_arguments = delta_map.get("arguments")
        if isinstance(delta_arguments, str):
            arguments = delta_arguments
        else:
            function = _as_mapping(delta_map.get("function"))
            if function is not None:
                function_arguments = function.get("arguments")
                if isinstance(function_arguments, str):
                    arguments = function_arguments

    tool_type = _first_str(candidate.get("tool_type"), candidate.get("type"))
    if tool_type and tool_type.startswith("response."):
        tool_type = None
    if tool_type in ("tool_call", "function_call"):
        tool_type = "function"

    if call_id is None and name is None and arguments is None:
        return None
    return call_id, name, arguments, tool_type


def _select_tool_call_candidate(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    item = _as_mapping(payload.get("item"))
    if item is not None:
        item_type = item.get("type")
        if isinstance(item_type, str) and ("tool" in item_type or "function" in item_type):
            return item
        if any(key in item for key in ("call_id", "tool_call_id", "arguments", "function", "name")):
            return item
    return payload


def _tool_call_key(call_id: str | None, name: str | None) -> str | None:
    if call_id:
        return f"id:{call_id}"
    if name:
        return f"name:{name}"
    return None


def _tool_call_arguments_mode(payload: Mapping[str, JsonValue]) -> Literal["append", "replace"]:
    event_type = payload.get("type")
    if isinstance(event_type, str):
        if event_type.endswith(".delta"):
            return "append"
        if event_type.endswith(".done"):
            return "replace"
        if event_type == "response.output_item.added":
            return "replace"
    return "replace"


def _as_mapping(value: JsonValue) -> Mapping[str, JsonValue] | None:
    if is_json_mapping(value):
        return value
    return None


def _first_str(*values: JsonValue) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _merge_tool_call_delta(tool_calls: list[ToolCallState], delta: ToolCallDelta) -> ToolCallState:
    while len(tool_calls) <= delta.index:
        tool_calls.append(ToolCallState(index=len(tool_calls)))
    tool_calls[delta.index].apply_delta(delta)
    return tool_calls[delta.index]


def _compact_tool_calls(tool_calls: list[ToolCallState]) -> list[ChatMessageToolCall]:
    cleaned: list[ChatMessageToolCall] = []
    for call in tool_calls:
        tool_call = call.to_message_tool_call()
        if tool_call is not None:
            cleaned.append(tool_call)
    return cleaned


def _pending_stream_value(emitted: str | None, current: str | None) -> str | None:
    if current is None:
        return None
    if emitted is None or emitted != current:
        return current
    return None


def _pending_stream_arguments(emitted: str, current: str) -> str | None:
    if not current or current == emitted:
        return None
    if not emitted:
        return current
    if current.startswith(emitted):
        suffix = current[len(emitted) :]
        return suffix or None

    return None
