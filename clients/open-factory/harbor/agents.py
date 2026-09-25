"""Harbor agents for Open Factory runs.

Thin subclasses of Harbor's built-in Claude Code and Codex agents that
1. expect the CLI to be pre-baked into the task image (``of-work/agents`` and
   the ``of-work/*-base`` images) and fail fast instead of spending minutes
   reinstalling it per trial,
2. default their model endpoint to agent-lb on the host, so a run needs no
   ``--ae`` flags on the OrbStack path,
3. on tasks with ``network_mode = "allowlist"``, narrow egress for the agent
   phase from the whole host to agent-lb's host:port. Harbor's allowlist takes
   hostnames only, so ``host.docker.internal`` alone would also expose every
   other service listening on the host, and
4. turn off the CLIs' web tools (server-side search sidesteps the allowlist).

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
- ``OF_HARBOR_REQUIRE_ALLOWLIST=1``: fail a trial whose task does not use
  ``network_mode = "allowlist"``, instead of running it with open egress.

Explicit ``--ae`` values always win over these defaults.
"""

from __future__ import annotations

import os
import shlex
from typing import Any
from urllib.parse import urlsplit

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import NetworkMode

DEFAULT_AGENT_LB_URL = "http://host.docker.internal:2455"
DEFAULT_KEY_FILE = "~/.agent-lb/of/of-harbor.key"
# Harbor's docker egress sidecar (a gost transparent proxy); its allowlist accepts host:port.
EGRESS_SIDECAR_SERVICE = "harbor-docker-egress-control-sidecar"
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


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def agent_lb_endpoint() -> str:
    parts = urlsplit(agent_lb_url())
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.hostname}:{port}"


async def pin_egress_to_agent_lb(environment: BaseEnvironment) -> None:
    """Allow only agent-lb's host:port for the rest of the trial.

    Runs at the start of the agent phase, after Harbor has applied the task's
    allowlist. Harbor restores the baseline after the agent phase only when the
    phase policy differs from it, so the narrowing usually lasts through the
    verifier too; test commands must not need the network.
    """
    if environment.network_policy.network_mode != NetworkMode.ALLOWLIST:
        if _flag("OF_HARBOR_REQUIRE_ALLOWLIST"):
            raise RuntimeError(
                f"task network_mode is {environment.network_policy.network_mode.value}; "
                'Open Factory tasks need network_mode = "allowlist" (OF_HARBOR_REQUIRE_ALLOWLIST=1)'
            )
        return
    result = await environment.service_exec(
        f"network-policy allow {shlex.quote(agent_lb_endpoint())}",
        service=EGRESS_SIDECAR_SERVICE,
        timeout_sec=60,
    )
    if result.return_code != 0:
        raise RuntimeError(f"could not narrow egress to agent-lb: {(result.stderr or result.stdout or '').strip()}")


def _with_defaults(extra_env: dict[str, str] | None, defaults: dict[str, str]) -> dict[str, str]:
    return {**defaults, **(extra_env or {})}


class PrebakedClaudeCode(ClaudeCode):
    """Claude Code routed through agent-lb; requires ``claude`` in the image."""

    def __init__(self, *args: Any, extra_env: dict[str, str] | None = None, **kwargs: Any):
        defaults = {"ANTHROPIC_BASE_URL": agent_lb_url()}
        if not (extra_env and {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} & extra_env.keys()):
            # Bearer (not x-api-key) so the key also validates on remote paths.
            defaults["ANTHROPIC_AUTH_TOKEN"] = agent_lb_key()
        # Server-side search reaches the internet past the egress allowlist; --ak overrides.
        kwargs.setdefault("disallowed_tools", "WebSearch,WebFetch")
        super().__init__(*args, extra_env=_with_defaults(extra_env, defaults), **kwargs)

    async def install(self, environment: BaseEnvironment) -> None:
        if await self._installed_claude_satisfies_version(environment):
            return
        if _flag("OF_HARBOR_ALLOW_INSTALL"):
            await super().install(environment)
            return
        raise RuntimeError(
            "claude is not pre-baked in this image (build of-work/agents, or set OF_HARBOR_ALLOW_INSTALL=1)"
        )

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await pin_egress_to_agent_lb(environment)
        await super().run(instruction, environment, context)


class PrebakedCodex(Codex):
    """Codex routed through agent-lb's /v1/responses; requires ``codex`` in the image."""

    def __init__(self, *args: Any, extra_env: dict[str, str] | None = None, **kwargs: Any):
        defaults = {"OPENAI_BASE_URL": f"{agent_lb_url()}/v1", "OPENAI_API_KEY": agent_lb_key()}
        # Server-side search reaches the internet past the egress allowlist; --ak overrides.
        kwargs.setdefault("web_search", "disabled")
        super().__init__(*args, extra_env=_with_defaults(extra_env, defaults), **kwargs)

    async def install(self, environment: BaseEnvironment) -> None:
        if await self._installed_codex_satisfies_version(environment):
            return
        if _flag("OF_HARBOR_ALLOW_INSTALL"):
            await super().install(environment)
            return
        raise RuntimeError(
            "codex is not pre-baked in this image (build of-work/agents, or set OF_HARBOR_ALLOW_INSTALL=1)"
        )

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await pin_egress_to_agent_lb(environment)
        await super().run(instruction, environment, context)
