from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from app.core.openai.images import V1ImageUsage
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping


def _coerce_int(value: JsonValue | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


_USAGE_TOKEN_FIELDS: Final[frozenset[str]] = frozenset({"input_tokens", "output_tokens", "total_tokens"})


def _extract_image_usage(response: Mapping[str, JsonValue]) -> V1ImageUsage | None:
    tool_usage = response.get("tool_usage")
    if not is_json_mapping(tool_usage):
        return None
    image_usage = tool_usage.get("image_gen")
    if not is_json_mapping(image_usage):
        return None
    input_tokens = _coerce_int(image_usage.get("input_tokens"))
    output_tokens = _coerce_int(image_usage.get("output_tokens"))
    total_tokens = _coerce_int(image_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    input_details_raw = image_usage.get("input_tokens_details")
    output_details_raw = image_usage.get("output_tokens_details")
    input_details = dict(input_details_raw) if is_json_mapping(input_details_raw) else None
    output_details = dict(output_details_raw) if is_json_mapping(output_details_raw) else None

    extra_usage: dict[str, JsonValue] = {}
    for key, value in image_usage.items():
        if key in _USAGE_TOKEN_FIELDS:
            continue
        if key in ("input_tokens_details", "output_tokens_details"):
            continue
        extra_usage[key] = value
    if (
        input_tokens is None
        and output_tokens is None
        and total_tokens is None
        and input_details is None
        and output_details is None
        and not extra_usage
    ):
        return None
    return V1ImageUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_tokens_details=input_details,
        output_tokens_details=output_details,
        **extra_usage,
    )


def _stash_image_usage_tokens(captured: dict[str, object], usage: V1ImageUsage) -> None:

    if usage.input_tokens is not None:
        captured["image_input_tokens"] = int(usage.input_tokens)
    if usage.output_tokens is not None:
        captured["image_output_tokens"] = int(usage.output_tokens)
    cached = _extract_cached_input_tokens(usage)
    if cached is not None:
        captured["image_cached_input_tokens"] = cached


def _extract_cached_input_tokens(usage: V1ImageUsage) -> int | None:
    details = usage.input_tokens_details
    if not isinstance(details, Mapping):
        return None
    raw = details.get("cached_tokens")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    return None
