from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.core.types import JsonObject, JsonValue
from app.core.utils.client_session import get_caller_seat


@dataclass
class EvalGuardState:
    key_name: str | None = None
    removed: list[str] = field(default_factory=list)


eval_guard_state: ContextVar[EvalGuardState | None] = ContextVar("eval_guard_state", default=None)


def is_eval_request() -> bool:
    state = eval_guard_state.get()
    seat = get_caller_seat()
    return (state is not None and state.key_name == "of-harbor") or bool(seat and seat.startswith("eval."))


def strip_eval_web_tools(body: JsonObject, provider: str) -> tuple[dict[str, JsonValue], list[str]]:
    result = dict(body)
    state = eval_guard_state.get()
    if not is_eval_request():
        return result, []
    tools = result.get("tools")
    if not isinstance(tools, list):
        return result, []
    prefixes = ("web_search", "web_fetch") if provider == "anthropic" else ("web_search",)
    kept: list[JsonValue] = []
    removed: list[str] = []
    names: set[str] = set()
    for tool in tools:
        tool_type = tool.get("type") if isinstance(tool, Mapping) else None
        if isinstance(tool_type, str) and tool_type.startswith(prefixes):
            removed.append(tool_type)
            name = tool.get("name")
            if isinstance(name, str):
                names.add(name)
        else:
            kept.append(tool)
    if kept:
        result["tools"] = kept
    else:
        result.pop("tools", None)
    choice = result.get("tool_choice")
    if provider == "anthropic" and isinstance(choice, Mapping) and choice.get("name") in names:
        result.pop("tool_choice", None)
    if state is not None:
        for tool_type in removed:
            if tool_type not in state.removed:
                state.removed.append(tool_type)
    return result, removed
