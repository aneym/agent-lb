from __future__ import annotations

import logging
import re
from typing import Any

from app.core.utils.client_session import get_caller_seat
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)

_SUFFIX = r"(?:\[[0-9a-z]+\])?$"
_STRICT_NAMES = r"fable-5-[12]|mythos-5-[12]|opus-5-5|sonnet-5-5"
_STRICT_MODEL = re.compile(rf"^claude-(?:{_STRICT_NAMES}){_SUFFIX}")
_SAMPLING_MODEL = re.compile(rf"^claude-(?:{_STRICT_NAMES}|haiku-5-5){_SUFFIX}")
_DISABLED_THINKING_MODEL = re.compile(rf"^claude-(?:fable-5-[12]|mythos-5-[12]|opus-5-5){_SUFFIX}")
_SAMPLING_FIELDS = ("temperature", "top_p", "top_k")
_FORCED_TOOL_CHOICES = frozenset({"any", "tool"})
_ADAPTIVE_KEPT_FIELDS = ("display",)


def shape_strict_params(model: str, body: dict[str, Any]) -> dict[str, Any]:
    name = model.strip().lower()
    if not _SAMPLING_MODEL.match(name):
        return body
    strict = bool(_STRICT_MODEL.match(name))
    changes: list[str] = []
    shaped = dict(body)
    for field in _SAMPLING_FIELDS:
        if field in shaped:
            shaped.pop(field)
            changes.append(f"dropped {field}")
    thinking = shaped.get("thinking")
    if isinstance(thinking, dict):
        if thinking.get("type") == "disabled" and _DISABLED_THINKING_MODEL.match(name):
            shaped.pop("thinking")
            changes.append("dropped thinking.disabled")
        elif thinking.get("type") == "enabled" and strict:
            adaptive: dict[str, Any] = {"type": "adaptive"}
            adaptive.update({key: thinking[key] for key in _ADAPTIVE_KEPT_FIELDS if key in thinking})
            shaped["thinking"] = adaptive
            changes.append("thinking.enabled to adaptive")
    choice = shaped.get("tool_choice")
    if strict and isinstance(choice, dict) and choice.get("type") in _FORCED_TOOL_CHOICES:
        auto: dict[str, Any] = {"type": "auto"}
        if "disable_parallel_tool_use" in choice:
            auto["disable_parallel_tool_use"] = choice["disable_parallel_tool_use"]
        shaped["tool_choice"] = auto
        changes.append(f"tool_choice {choice.get('type')} to auto")
    _log_assistant_tail(model, shaped)
    if not changes:
        return body
    logger.info(
        "Anthropic strict params shaped model=%s request_id=%s changes=%s",
        model,
        get_request_id(),
        ",".join(changes),
    )
    return shaped


def _log_assistant_tail(model: str, body: dict[str, Any]) -> None:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return
    last = messages[-1]
    if isinstance(last, dict) and last.get("role") == "assistant":
        logger.info(
            "Anthropic strict body ends on assistant turn model=%s seat=%s request_id=%s",
            model,
            get_caller_seat() or "-",
            get_request_id(),
        )
