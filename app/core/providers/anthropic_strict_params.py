from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_STRICT_MODEL = re.compile(r"^claude-(?:fable-5-[12]|mythos-5-[12]|opus-5-5)(?:\[[0-9a-z]+\])?$")
_SAMPLING_FIELDS = ("temperature", "top_p", "top_k")
_FORCED_TOOL_CHOICES = frozenset({"any", "tool"})


def is_strict_params_model(model: str) -> bool:
    return bool(_STRICT_MODEL.match(model.strip().lower()))


def shape_strict_params(model: str, body: dict[str, Any]) -> dict[str, Any]:
    if not is_strict_params_model(model):
        return body
    changes: list[str] = []
    shaped = dict(body)
    for field in _SAMPLING_FIELDS:
        if field in shaped:
            shaped.pop(field)
            changes.append(f"dropped {field}")
    thinking = shaped.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        shaped.pop("thinking")
        changes.append("dropped thinking.disabled")
    choice = shaped.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") in _FORCED_TOOL_CHOICES:
        auto: dict[str, Any] = {"type": "auto"}
        if "disable_parallel_tool_use" in choice:
            auto["disable_parallel_tool_use"] = choice["disable_parallel_tool_use"]
        shaped["tool_choice"] = auto
        changes.append(f"tool_choice {choice.get('type')} to auto")
    if not changes:
        return body
    logger.info("Anthropic strict params shaped model=%s changes=%s", model, ",".join(changes))
    return shaped
