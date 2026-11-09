from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from app.core.anthropic.oauth import ANTHROPIC_PROVIDER_NAME
from app.core.providers.anthropic_strict_params import shape_strict_params
from app.core.providers.kimi import KIMI_PROVIDER_NAME
from app.core.providers.kimi_tool_changes import drop_tool_change_betas, flatten_tool_changes
from app.core.providers.kimi_tool_history import normalize_tool_history
from app.core.providers.openrouter import is_deepseek_model, map_deepseek_body


def prepare_provider_body(
    provider_name: str, model: str, body: dict[str, Any], headers: MutableMapping[str, str]
) -> dict[str, Any]:
    if provider_name == "openrouter" and is_deepseek_model(model):
        return map_deepseek_body(body)
    if provider_name == KIMI_PROVIDER_NAME:
        drop_tool_change_betas(headers)
        return normalize_tool_history(flatten_tool_changes(body))
    if provider_name == ANTHROPIC_PROVIDER_NAME:
        return shape_strict_params(model, body)
    return body
