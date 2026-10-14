

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

EGRESS_SIDECAR_SERVICE = "harbor-docker-egress-control-sidecar"


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


    def __init__(self, *args: Any, extra_env: dict[str, str] | None = None, **kwargs: Any):
        defaults = {"ANTHROPIC_BASE_URL": agent_lb_url()}
        if not (extra_env and {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} & extra_env.keys()):

            defaults["ANTHROPIC_AUTH_TOKEN"] = agent_lb_key()

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


    def __init__(self, *args: Any, extra_env: dict[str, str] | None = None, **kwargs: Any):
        defaults = {"OPENAI_BASE_URL": f"{agent_lb_url()}/v1", "OPENAI_API_KEY": agent_lb_key()}

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
