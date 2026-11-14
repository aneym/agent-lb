from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from app.core.config.settings import get_settings
from app.core.providers import ANTHROPIC_PROVIDER_NAME
from app.core.utils.client_session import CHILD_THREAD_PREFIX, get_caller_seat, get_thread_id
from app.modules.proxy.anthropic_outbound_headers import merge_anthropic_beta_header

logger = logging.getLogger(__name__)

ONE_SHOT_ENTRYPOINTS = frozenset({"sdk-cli"})
SHORT_GAP_SEATS = frozenset({"rails-runner"})
SHORT_GAP_OPUS_AGENTS = frozenset({"recruiter"})

_ENTRYPOINT = re.compile(r"^claude-cli/\S+ \(external, ([\w.-]+)")


def apply_cache_tier(
    body: dict[str, Any],
    headers: dict[str, str],
    inbound_headers: Mapping[str, str],
    provider_name: str,
) -> str | None:
    if provider_name == "kimi":
        return requested_cache_tier(body)
    if provider_name != ANTHROPIC_PROVIDER_NAME:
        return None
    target = _target_tier(inbound_headers, str(body.get("model") or ""))
    if target is None:
        return requested_cache_tier(body)
    controls = _cache_controls(body)
    if not controls:
        return requested_cache_tier(body)
    if any(control.get("type") != "ephemeral" for control in controls):
        logger.warning("Cache breakpoint is not ephemeral; leaving request tier unchanged")
        return requested_cache_tier(body)
    for control in controls:
        control["ttl"] = target
    if target == "1h":
        merge_anthropic_beta_header(headers, ["extended-cache-ttl-2025-04-11"])
    return target


def client_entrypoint(inbound_headers: Mapping[str, str]) -> str | None:
    user_agent = next((value for key, value in inbound_headers.items() if key.lower() == "user-agent"), "")
    match = _ENTRYPOINT.match(user_agent) if isinstance(user_agent, str) else None
    return match.group(1) if match else None


def _short_gap(inbound_headers: Mapping[str, str], model: str) -> bool:
    if model.startswith("claude-fable-") and (get_thread_id() or "").startswith(CHILD_THREAD_PREFIX):
        return True
    agent = next((value for key, value in inbound_headers.items() if key.lower() == "x-agent-lb-agent"), "")
    if model.startswith("claude-opus-") and isinstance(agent, str) and agent.strip().lower() in SHORT_GAP_OPUS_AGENTS:
        return True
    return (get_caller_seat() or "") in SHORT_GAP_SEATS


def _target_tier(inbound_headers: Mapping[str, str], model: str) -> str | None:
    settings = get_settings()
    if settings.anthropic_one_shot_cache_5m_enabled and client_entrypoint(inbound_headers) in ONE_SHOT_ENTRYPOINTS:
        return "5m"
    if settings.anthropic_short_gap_cache_5m_enabled and _short_gap(inbound_headers, model):
        return "5m"
    if settings.anthropic_session_cache_1h_enabled:
        return "1h"
    agent_id = next((value for key, value in inbound_headers.items() if key.lower() == "x-claude-code-agent-id"), "")
    if settings.anthropic_helper_cache_1h_enabled and isinstance(agent_id, str) and agent_id.strip():
        return "1h"
    return None


def _cache_controls(body: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[Any] = [body]
    for key in ("system", "tools"):
        if isinstance(body.get(key), list):
            blocks.extend(body[key])
    pending: list[Any] = [message for message in body.get("messages", []) if isinstance(message, dict)]
    while pending:
        item = pending.pop()
        if isinstance(item, dict) and isinstance(item.get("content"), list):
            blocks.extend(item["content"])
            pending.extend(item["content"])
    return [
        block["cache_control"]
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("cache_control"), dict)
    ]


def requested_cache_tier(body: dict[str, Any]) -> str | None:
    controls = []
    pending: list[Any] = [body]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            control = value.get("cache_control")
            if isinstance(control, dict) and control.get("type") == "ephemeral":
                ttl = control.get("ttl", "5m")
                controls.append(ttl if ttl in ("5m", "1h") else "mx")
            pending.extend(v for k, v in value.items() if k != "cache_control")
        elif isinstance(value, list):
            pending.extend(value)
    tiers = set(controls)
    return next(iter(tiers)) if len(tiers) == 1 else "mx" if tiers else None
