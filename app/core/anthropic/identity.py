from __future__ import annotations

CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

CLAUDE_CODE_CLIENTS = frozenset({"claude-cli", "claude"})

CLAUDE_CODE_ONLY_MESSAGE = (
    "Claude subscription accounts serve Claude Code clients only (claude-cli: the CLI, Claude Desktop, "
    "the Agent SDK). Send other traffic with an Anthropic API key."
)


def is_claude_code_client(useragent_group: str | None) -> bool:
    return useragent_group in CLAUDE_CODE_CLIENTS
