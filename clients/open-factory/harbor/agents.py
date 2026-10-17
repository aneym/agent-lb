

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.installed.codex import Codex
from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import NetworkMode

DEFAULT_AGENT_LB_URL = "http://host.docker.internal:2455"
DEFAULT_KEY_FILE = "~/.agent-lb/of/of-harbor.key"

EGRESS_SIDECAR_SERVICE = "harbor-docker-egress-control-sidecar"
EGRESS_TABLE = "gost_egress"
PORT_PIN_CHAIN = "of_agent_lb_port"
GOST_MARK = 114514


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


def agent_lb_host_port() -> tuple[str, int]:
    parts = urlsplit(agent_lb_url())
    if not parts.hostname:
        raise RuntimeError(f"OF_AGENT_LB_URL has no host: {agent_lb_url()!r}")
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def egress_pin_command() -> str:
    host, port = agent_lb_host_port()
    chain = f"inet {EGRESS_TABLE} {PORT_PIN_CHAIN}"
    rule = (
        f"meta l4proto tcp fib daddr type != local meta mark != {GOST_MARK} "
        f"tcp dport != {{ 53, {port} }} reject with tcp reset"
    )
    return " && ".join(
        [
            f"network-policy allow {shlex.quote(host)}",
            f"nft add chain {chain} {shlex.quote('{ type filter hook output priority -150; policy accept; }')}",
            f"nft flush chain {chain}",
            f"nft add rule {chain} {shlex.quote(rule)}",
        ]
    )


async def pin_egress_to_agent_lb(environment: BaseEnvironment) -> None:

    if environment.network_policy.network_mode != NetworkMode.ALLOWLIST:
        if _flag("OF_HARBOR_REQUIRE_ALLOWLIST"):
            raise RuntimeError(
                f"task network_mode is {environment.network_policy.network_mode.value}; "
                'Open Factory tasks need network_mode = "allowlist" (OF_HARBOR_REQUIRE_ALLOWLIST=1)'
            )
        return
    result = await environment.service_exec(
        egress_pin_command(),
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


class PrebakedPiDurable(BaseInstalledAgent):
    def __init__(self, *args: Any, reasoning_effort: str = "medium", extra_env: dict[str, str] | None = None, **kwargs: Any):
        defaults = {"AGENT_LB_URL": agent_lb_url(), "AGENT_LB_API_KEY": agent_lb_key()}
        self.reasoning_effort = reasoning_effort
        super().__init__(*args, extra_env=_with_defaults(extra_env, defaults), version="1.3.0", **kwargs)

    @staticmethod
    def name() -> str:
        return "pi-durable"

    async def install(self, environment: BaseEnvironment) -> None:
        check = "test -f /opt/pi-durable/pd-run.ts && /opt/pi-durable/bin/node -e 'const [major, minor] = process.versions.node.split(\".\").map(Number); if (major < 22 || (major === 22 && minor < 6)) process.exit(1); console.log(process.version)'"
        result = await environment.exec(command=check)
        if result.return_code != 0:
            bundle = Path(os.environ.get("OF_PI_DURABLE_BUNDLE", "~/eval-scratch/pi-durable-1.3.0.tgz")).expanduser()
            if not bundle.is_file():
                raise RuntimeError(f"pi-durable runner missing and cache bundle not found: {bundle}; run build-images.sh pi-durable")
            await environment.upload_file(bundle, "/installed-agent/pi-durable.tgz")
            result = await environment.exec(command="tar -xzf /installed-agent/pi-durable.tgz -C /opt && " + check, user="root")
            if result.return_code != 0:
                raise RuntimeError("pi-durable bundle installation failed; runner or bundled Node is unavailable")

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await pin_egress_to_agent_lb(environment)
        if not self.model_name:
            raise RuntimeError("pi-durable requires a concrete model")
        seat = self._extra_env.get("AGENT_LB_SEAT", "")
        if not seat.startswith("eval."):
            raise RuntimeError("pi-durable requires an eval.* AGENT_LB_SEAT")
        log_dir = str(self.environment_logs_dir)
        args = ["/opt/pi-durable/bin/node", "--experimental-strip-types", "/opt/pi-durable/pd-run.ts",
                "--provider", "lb-responses", "--model", self.model_name,
                "--effort", self.reasoning_effort, "--cwd", "/app",
                "--db", log_dir + "/pd.sqlite", "--pd-dir", log_dir + "/pd.sqlite.d",
                "--request-id", seat, "--last", log_dir + "/last.txt", "--prompt-file", "-"]
        command = ("mkdir -p " + shlex.quote(log_dir) + " && printf %s " + shlex.quote(instruction)
                   + " | " + shlex.join(args) + " > " + shlex.quote(log_dir + "/stdout.log")
                   + " 2> " + shlex.quote(log_dir + "/stderr.log"))
        result = await environment.exec(command=command, cwd="/app", env=self._extra_env)
        if result.return_code != 0:
            raise RuntimeError(f"pi-durable exited {result.return_code}; see agent/stderr.log")
