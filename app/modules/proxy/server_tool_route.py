from __future__ import annotations

from app.core.anthropic.models import AnthropicDefinedToolDefinition, AnthropicMessageRequest
from app.core.routing_table import read_routing_table

SERVER_TOOL_ALIAS = "haiku-latest"


def carries_server_tools(payload: AnthropicMessageRequest) -> bool:
    return bool(payload.tools) and any(isinstance(tool, AnthropicDefinedToolDefinition) for tool in payload.tools or ())


def server_tool_claude_model(payload: AnthropicMessageRequest) -> str | None:
    if not carries_server_tools(payload):
        return None
    pinned = read_routing_table()["aliases"][SERVER_TOOL_ALIAS]["pinned"]
    if not isinstance(pinned, str) or not pinned.startswith("claude-"):
        raise ValueError(f"routing table alias {SERVER_TOOL_ALIAS} must pin a claude- model")
    return pinned
