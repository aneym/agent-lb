from __future__ import annotations

import logging
from typing import Any, Mapping

from app.core.config.settings import get_settings
from app.core.providers import ANTHROPIC_PROVIDER_NAME
from app.modules.proxy.anthropic_outbound_headers import merge_anthropic_beta_header

logger = logging.getLogger(__name__)


def upgrade_helper_cache(
    body: dict[str, Any],
    headers: dict[str, str],
    inbound_headers: Mapping[str, str],
    provider_name: str,
) -> str | None:

    if provider_name == "kimi":
        controls = []
        pending = [body]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                control = value.get("cache_control")
                if isinstance(control, dict) and control.get("type") == "ephemeral":
                    ttl = control.get("ttl", "5m")
                    controls.append(ttl if ttl in ("5m", "1h") else "mixed")
                pending.extend(v for k, v in value.items() if k != "cache_control")
            elif isinstance(value, list):
                pending.extend(value)
        tiers = set(controls)
        return next(iter(tiers)) if len(tiers) == 1 else "mixed" if tiers else None

    agent_id = next((value for key, value in inbound_headers.items() if key.lower() == "x-claude-code-agent-id"), "")
    if (
        provider_name != ANTHROPIC_PROVIDER_NAME
        or not get_settings().anthropic_helper_cache_1h_enabled
        or not isinstance(agent_id, str)
        or not agent_id.strip()
    ):
        return None
    blocks: list[Any] = [body]
    for key in ("system", "tools"):
        if isinstance(body.get(key), list):
            blocks.extend(body[key])
    for message in body.get("messages", []):
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            blocks.extend(message["content"])
    upgraded = 0
    for block in blocks:
        if not isinstance(block, dict) or "cache_control" not in block:
            continue
        control = block["cache_control"]
        if not isinstance(control, dict) or control.get("type") != "ephemeral":
            logger.warning("Helper cache breakpoint is invalid; leaving request unchanged")
            return None
    for block in blocks:
        if isinstance(block, dict) and isinstance(block.get("cache_control"), dict):
            block["cache_control"]["ttl"] = "1h"
            upgraded += 1
    if upgraded:
        merge_anthropic_beta_header(headers, ["extended-cache-ttl-2025-04-11"])
        return "1h"
    return None
