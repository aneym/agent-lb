"""Harbor agents for Open Factory runs.

Thin subclasses of Harbor's built-in Claude Code and Codex agents that
1. expect the CLI to be pre-baked into the task image (``of-work/agents`` and
   the ``of-work/*-base`` images) and fail fast instead of spending minutes
   reinstalling it per trial, and
2. default their model endpoint to agent-lb on the host, so a run needs no
   ``--ae`` flags on the OrbStack path.

Usage (from this directory's parent, or with it on PYTHONPATH):

    PYTHONPATH=clients/open-factory/harbor harbor run -p <task> \
        -a agents:PrebakedClaudeCode -m claude-sonnet-5
    PYTHONPATH=clients/open-factory/harbor harbor run -p <task> \
        -a agents:PrebakedCodex -m gpt-6-luna

Environment knobs (read on the host, at agent construction):
- ``OF_AGENT_LB_URL``: agent-lb as seen from the container
  (default ``http://host.docker.internal:2455``).
- ``OF_HARBOR_KEY``: the agent-lb key (sk-clb-...) the agents send, so requests
  are attributed to it (``request_log_query.py --key of-harbor``). When unset,
  the key is read from ``OF_HARBOR_KEY_FILE`` (default
  ``~/.agent-lb/of/of-harbor.key``); with neither, a placeholder is sent.
- ``OF_HARBOR_ALLOW_INSTALL=1``: fall back to Harbor's normal installer when the
  CLI is missing, instead of failing the trial.

Explicit ``--ae`` values always win over these defaults.
"""

from __future__ import annotations

import os
from typing import Any

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment

DEFAULT_AGENT_LB_URL = "http://host.docker.internal:2455"
DEFAULT_KEY_FILE = "~/.agent-lb/of/of-harbor.key"
# Sent when no key is configured: Harbor and the CLIs need a non-empty credential,
# and the request is then unattributed.
PLACEHOLDER_KEY = "agent-lb-local"


def agent_lb_url() -> str:
    return os.environ.get("OF_AGENT_LB_URL", DEFAULT_AGENT_LB_URL).rstrip("/")


def agent_lb_key() -> str:
    key = os.environ.get("OF_HARBOR_KEY", "").strip()
    if key:
        return key
    try:
        with open(os.path.expanduser(os.environ.get("OF_HARBOR_KEY_FILE", DEFAULT_KEY_FILE))) as handle:
            return handle.read().strip() or PLACEHOLDER_KEY
    except OSError:
        return PLACEHOLDER_KEY


def _install_allowed() -> bool:
    return os.environ.get("OF_HARBOR_ALLOW_INSTALL", "").strip().lower() in {"1", "true", "yes"}


def _with_defaults(extra_env: dict[str, str] | None, defaults: dict[str, str]) -> dict[str, str]:
    return {**defaults, **(extra_env or {})}


class PrebakedClaudeCode(ClaudeCode):
    """Claude Code routed through agent-lb; requires ``claude`` in the image."""

    def __init__(self, *args: Any, extra_env: dict[str, str] | None = None, **kwargs: Any):
        defaults = {"ANTHROPIC_BASE_URL": agent_lb_url()}
        if not (extra_env and {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} & extra_env.keys()):
            # Bearer (not x-api-key) so the key also validates on remote paths.
            defaults["ANTHROPIC_AUTH_TOKEN"] = agent_lb_key()
        super().__init__(*args, extra_env=_with_defaults(extra_env, defaults), **kwargs)

    async def install(self, environment: BaseEnvironment) -> None:
        if await self._installed_claude_satisfies_version(environment):
            return
        if _install_allowed():
            await super().install(environment)
            return
        raise RuntimeError(
            "claude is not pre-baked in this image (build of-work/agents, or set OF_HARBOR_ALLOW_INSTALL=1)"
        )


class PrebakedCodex(Codex):
    """Codex routed through agent-lb's /v1/responses; requires ``codex`` in the image."""

    def __init__(self, *args: Any, extra_env: dict[str, str] | None = None, **kwargs: Any):
        defaults = {"OPENAI_BASE_URL": f"{agent_lb_url()}/v1", "OPENAI_API_KEY": agent_lb_key()}
        super().__init__(*args, extra_env=_with_defaults(extra_env, defaults), **kwargs)

    async def install(self, environment: BaseEnvironment) -> None:
        if await self._installed_codex_satisfies_version(environment):
            return
        if _install_allowed():
            await super().install(environment)
            return
        raise RuntimeError(
            "codex is not pre-baked in this image (build of-work/agents, or set OF_HARBOR_ALLOW_INSTALL=1)"
        )
