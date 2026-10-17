#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

START = "<!-- agent-lb:coding-agent-routing:start -->"
END = "<!-- agent-lb:coding-agent-routing:end -->"
CODEX_START = "<!-- agent-lb:codex-agents:start -->"
CODEX_END = "<!-- agent-lb:codex-agents:end -->"
MODEL = "opus"
EFFORT_LEVEL = "medium"
HAIKU_ENV = "ANTHROPIC_DEFAULT_HAIKU_MODEL"
POLICY_SNAPSHOT = Path(".agent-lb/managed/coding-agents/policy-snapshot.json")




SONNET_ENV = "ANTHROPIC_DEFAULT_SONNET_MODEL"
SONNET_MODEL = "claude-sonnet-5-5"
SONNET_ID = re.compile(r"claude-sonnet-\d+(?:-\d{1,2})?")
SOL_MODEL = "gpt-6.1-sol"
SOL_ID = re.compile(r"gpt-\d+(?:\.\d+)*-sol")
MANAGED_AGENTS = (
    (
        Path(".agent-lb/managed/coding-agents/codex-seat-home.py"),
        Path(".agent-lb/managed/coding-agents/codex-seat-home"),
        "agent-lb:codex-seat-home:v1\n",
        Path("codex-seat-home.py"),
    ),
    (
        Path(".codex/rules/port-kill.rules"),
        Path(".agent-lb/managed/coding-agents/codex-port-kill-rules"),
        "agent-lb:codex-port-kill-rules:v1\n",
        Path("codex/port-kill.rules"),
    ),
    (
        Path(".claude/agents/computer-use.md"),
        Path(".agent-lb/managed/coding-agents/computer-use"),
        "agent-lb:computer-use:v1\n",
        Path("agents/computer-use.md"),
    ),
    (
        Path(".claude/agents/frontend-designer.md"),
        Path(".agent-lb/managed/coding-agents/frontend-designer"),
        "agent-lb:frontend-designer:v1\n",
        Path("agents/frontend-designer.md"),
    ),
    (
        Path(".claude/agents/planner.md"),
        Path(".agent-lb/managed/coding-agents/planner"),
        "agent-lb:planner:v1\n",
        Path("agents/planner.md"),
    ),
    (
        Path(".claude/agents/plan-reviewer.md"),
        Path(".agent-lb/managed/coding-agents/plan-reviewer"),
        "agent-lb:plan-reviewer:v1\n",
        Path("agents/plan-reviewer.md"),
    ),
    (
        Path(".claude/agents/codex-sol.md"),
        Path(".agent-lb/managed/coding-agents/codex-sol"),
        "agent-lb:codex-sol:v1\n",
        Path("agents/codex-sol.md"),
    ),
    (
        Path(".claude/agents/Explore.md"),
        Path(".agent-lb/managed/coding-agents/Explore"),
        "agent-lb:Explore:v1\n",
        Path("agents/Explore.md"),
    ),
    (
        Path(".claude/agents/opus-seat.md"),
        Path(".agent-lb/managed/coding-agents/opus-seat"),
        "agent-lb:opus-seat:v1\n",
        Path("agents/opus-seat.md"),
    ),
    (
        Path(".claude/agents/opus-seat-full.md"),
        Path(".agent-lb/managed/coding-agents/opus-seat-full"),
        "agent-lb:opus-seat-full:v1\n",
        Path("agents/opus-seat-full.md"),
    ),
    (
        Path(".claude/agents/cursor-seat.md"),
        Path(".agent-lb/managed/coding-agents/cursor-seat"),
        "agent-lb:cursor-seat:v1\n",
        Path("agents/cursor-seat.md"),
    ),
    (
        Path(".claude/agents/devin-seat.md"),
        Path(".agent-lb/managed/coding-agents/devin-seat"),
        "agent-lb:devin-seat:v1\n",
        Path("agents/devin-seat.md"),
    ),
    (
        Path(".claude/agents/muse-seat.md"),
        Path(".agent-lb/managed/coding-agents/muse-seat"),
        "agent-lb:muse-seat:v1\n",
        Path("agents/muse-seat.md"),
    ),
    (
        Path(".claude/agents/muse-seat-local.md"),
        Path(".agent-lb/managed/coding-agents/muse-seat-local"),
        "agent-lb:muse-seat-local:v1\n",
        Path("agents/muse-seat-local.md"),
    ),
    (
        Path(".claude/agents/pi-durable-seat.md"),
        Path(".agent-lb/managed/coding-agents/pi-durable-seat"),
        "agent-lb:pi-durable-seat:v1\n",
        Path("agents/pi-durable-seat.md"),
    ),
    (
        Path(".claude/agents/codex-verifier.md"),
        Path(".agent-lb/managed/coding-agents/codex-verifier"),
        "agent-lb:codex-verifier:v1\n",
        Path("agents/codex-verifier.md"),
    ),
    (
        Path(".claude/agents/codex-test-runner.md"),
        Path(".agent-lb/managed/coding-agents/codex-test-runner"),
        "agent-lb:codex-test-runner:v1\n",
        Path("agents/codex-test-runner.md"),
    ),
    (
        Path(".claude/agents/verifier.md"),
        Path(".agent-lb/managed/coding-agents/verifier"),
        "agent-lb:verifier:v1\n",
        Path("agents/verifier.md"),
    ),
    (
        Path(".claude/agents/gpt-implementer.md"),
        Path(".agent-lb/managed/coding-agents/gpt-implementer"),
        "agent-lb:gpt-implementer:v1\n",
        Path("agents/gpt-implementer.md"),
    ),
    (
        Path(".claude/agents/gpt-explorer.md"),
        Path(".agent-lb/managed/coding-agents/gpt-explorer"),
        "agent-lb:gpt-explorer:v1\n",
        Path("agents/gpt-explorer.md"),
    ),
    (
        Path(".claude/agents/sol-consult.md"),
        Path(".agent-lb/managed/coding-agents/sol-consult"),
        "agent-lb:sol-consult:v1\n",
        Path("agents/sol-consult.md"),
    ),
    (
        Path(".claude/agents/astra-consult.md"),
        Path(".agent-lb/managed/coding-agents/astra-consult"),
        "agent-lb:astra-consult:v1\n",
        Path("agents/astra-consult.md"),
    ),
    (
        Path(".claude/agents/fable-orchestrator.md"),
        Path(".agent-lb/managed/coding-agents/fable-orchestrator"),
        "agent-lb:fable-orchestrator:v1\n",
        Path("agents/fable-orchestrator.md"),
    ),
    (
        Path(".claude/agents/sonnet-implementer.md"),
        Path(".agent-lb/managed/coding-agents/sonnet-implementer"),
        "agent-lb:sonnet-implementer:v1\n",
        Path("agents/sonnet-implementer.md"),
    ),
    (
        Path(".claude/agents/effort-xhigh.md"),
        Path(".agent-lb/managed/coding-agents/effort-xhigh"),
        "agent-lb:effort-xhigh:v1\n",
        Path("agents/effort-xhigh.md"),
    ),
    (
        Path(".claude/agents/host-relay.md"),
        Path(".agent-lb/managed/coding-agents/host-relay"),
        "agent-lb:host-relay:v1\n",
        Path("agents/host-relay.md"),
    ),
    (
        Path(".claude/agents/gpt-implementer-local.md"),
        Path(".agent-lb/managed/coding-agents/gpt-implementer-local"),
        "agent-lb:gpt-implementer-local:v1\n",
        Path("agents/gpt-implementer-local.md"),
    ),
    (
        Path(".claude/agents/gpt-explorer-local.md"),
        Path(".agent-lb/managed/coding-agents/gpt-explorer-local"),
        "agent-lb:gpt-explorer-local:v1\n",
        Path("agents/gpt-explorer-local.md"),
    ),
    (
        Path(".claude/agents/sonnet-implementer-local.md"),
        Path(".agent-lb/managed/coding-agents/sonnet-implementer-local"),
        "agent-lb:sonnet-implementer-local:v1\n",
        Path("agents/sonnet-implementer-local.md"),
    ),
    (
        Path(".claude/agents/verifier-local.md"),
        Path(".agent-lb/managed/coding-agents/verifier-local"),
        "agent-lb:verifier-local:v1\n",
        Path("agents/verifier-local.md"),
    ),
    (
        Path(".claude/agents/opus-seat-local.md"),
        Path(".agent-lb/managed/coding-agents/opus-seat-local"),
        "agent-lb:opus-seat-local:v1\n",
        Path("agents/opus-seat-local.md"),
    ),
    (
        Path(".claude/agents/codex-sol-local.md"),
        Path(".agent-lb/managed/coding-agents/codex-sol-local"),
        "agent-lb:codex-sol-local:v1\n",
        Path("agents/codex-sol-local.md"),
    ),
    (
        Path(".claude/agents/codex-test-runner-local.md"),
        Path(".agent-lb/managed/coding-agents/codex-test-runner-local"),
        "agent-lb:codex-test-runner-local:v1\n",
        Path("agents/codex-test-runner-local.md"),
    ),
    (
        Path(".claude/agents/computer-use-local.md"),
        Path(".agent-lb/managed/coding-agents/computer-use-local"),
        "agent-lb:computer-use-local:v1\n",
        Path("agents/computer-use-local.md"),
    ),
    (
        Path(".claude/hooks/subagent-closeout.py"),
        Path(".agent-lb/managed/coding-agents/subagent-closeout"),
        "agent-lb:subagent-closeout:v1\n",
        Path("hooks/subagent-closeout.py"),
    ),
    (
        Path(".claude/hooks/seat-guard.py"),
        Path(".agent-lb/managed/coding-agents/seat-guard"),
        "agent-lb:seat-guard:v1\n",
        Path("hooks/seat-guard.py"),
    ),
    (
        Path(".claude/hooks/workflow-seat-guard.py"),
        Path(".agent-lb/managed/coding-agents/workflow-seat-guard"),
        "agent-lb:workflow-seat-guard:v1\n",
        Path("hooks/workflow-seat-guard.py"),
    ),
    (
        Path(".claude/hooks/workflow-readback.py"),
        Path(".agent-lb/managed/coding-agents/workflow-readback"),
        "agent-lb:workflow-readback:v1\n",
        Path("hooks/workflow-readback.py"),
    ),
    (
        Path(".claude/hooks/factory-context.py"),
        Path(".agent-lb/managed/coding-agents/factory-context"),
        "agent-lb:factory-context:v1\n",
        Path("hooks/factory-context.py"),
    ),
    (
        Path(".claude/hooks/compact-checkpoint.py"),
        Path(".agent-lb/managed/coding-agents/compact-checkpoint"),
        "agent-lb:compact-checkpoint:v1\n",
        Path("hooks/compact-checkpoint.py"),
    ),
    (


        Path(".claude/hooks/wide-scan-guard.sh"),
        Path(".agent-lb/managed/coding-agents/wide-scan-guard"),
        "agent-lb:wide-scan-guard:v1\n",
        Path("hooks/wide-scan-guard.sh"),
    ),
    (


        Path(".claude/hooks/agent-lb-bootout-guard.sh"),
        Path(".agent-lb/managed/coding-agents/agent-lb-bootout-guard"),
        "agent-lb:agent-lb-bootout-guard:v1\n",
        Path("hooks/agent-lb-bootout-guard.sh"),
    ),
    (
        Path(".claude/hooks/railway-vars-guard.sh"),
        Path(".agent-lb/managed/coding-agents/railway-vars-guard"),
        "agent-lb:railway-vars-guard:v1\n",
        Path("hooks/railway-vars-guard.sh"),
    ),
    (


        Path(".claude/hooks/dangerous-command-guard.sh"),
        Path(".agent-lb/managed/coding-agents/dangerous-command-guard"),
        "agent-lb:dangerous-command-guard:v1\n",
        Path("hooks/dangerous-command-guard.sh"),
    ),
    (

        Path(".claude/hooks/link-cli-guard.sh"),
        Path(".agent-lb/managed/coding-agents/link-cli-guard"),
        "agent-lb:link-cli-guard:v1\n",
        Path("hooks/link-cli-guard.sh"),
    ),
    (
        Path(".claude/hooks/workflow-relay-guard.py"),
        Path(".agent-lb/managed/coding-agents/workflow-relay-guard"),
        "agent-lb:workflow-relay-guard:v1\n",
        Path("hooks/workflow-relay-guard.py"),
    ),
    (

        Path(".claude/hooks/plutil-guard.sh"),
        Path(".agent-lb/managed/coding-agents/plutil-guard"),
        "agent-lb:plutil-guard:v1\n",
        Path("hooks/plutil-guard.sh"),
    ),
    (

        Path(".claude/hooks/kill-scope-guard.py"),
        Path(".agent-lb/managed/coding-agents/kill-scope-guard"),
        "agent-lb:kill-scope-guard:v1\n",
        Path("hooks/kill-scope-guard.py"),
    ),
    (
        Path(".claude/hooks/hook-dispatch.py"),
        Path(".agent-lb/managed/coding-agents/hook-dispatch"),
        "agent-lb:hook-dispatch:v1\n",
        Path("hooks/hook-dispatch.py"),
    ),
    (
        Path(".claude/hooks/hook-dispatch-parity.py"),
        Path(".agent-lb/managed/coding-agents/hook-dispatch-parity"),
        "agent-lb:hook-dispatch-parity:v1\n",
        Path("hooks/hook-dispatch-parity.py"),
    ),
)



ADOPTED_KEEP = (Path(".claude/hooks/wide-scan-guard.sh"), Path(".claude/hooks/railway-vars-guard.sh"),
                Path(".claude/hooks/agent-lb-bootout-guard.sh"), Path(".claude/hooks/dangerous-command-guard.sh"),
                Path(".claude/hooks/link-cli-guard.sh"), Path(".claude/hooks/workflow-relay-guard.py"))




RETIRED_AGENTS = ("astra", "implementer")


ROUTING_TABLE = Path(".agent-lb/managed/coding-agents/routing-table.json")
ROUTING_TABLE_OWNER = (Path(".agent-lb/managed/coding-agents/routing-table"), "agent-lb:routing-table:v1\n")

POLICY_DIR = Path(".agents/policy/coding-agents")
MODELS_MARKER = "<!-- GENERATED by agent-lb install-policy"
POLICY_FILES = (
    "FINISH.md",
    "ROUTING.md",
    "claude-adapter.md",
    "install-policy.py",
    "verify-routing",
    "routing-table.json",
    "codex-seat-home.py",
)
LEGACY_HEADINGS = (
    "Coding-agent routing",
    "Orchestration — Fable architects, the fleet executes",
)
MODELS_FULL = Path(".claude/docs/models-full.md")
CORE_LINE = re.compile(r"<!-- core:(begin|end) ([a-z0-9][a-z0-9-]*) -->")


def split_core(text: str) -> tuple[str | None, str]:
    blocks: list[list[str]] = []
    full: list[str] = []
    names: set[str] = set()
    open_name: str | None = None
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        stripped = line.rstrip("\r\n")
        if "<!-- core:" not in stripped:
            full.append(line)
            if open_name is not None:
                blocks[-1].append(line)
            continue
        match = CORE_LINE.fullmatch(stripped)
        if not match:
            raise ValueError(f"line {number}: malformed core marker {stripped!r}")
        kind, name = match.groups()
        if kind == "begin":
            if open_name is not None:
                raise ValueError(f"line {number}: core block {name!r} opens inside {open_name!r}")
            if name in names:
                raise ValueError(f"line {number}: core block {name!r} is not unique")
            names.add(name)
            open_name = name
            blocks.append([])
        else:
            if open_name != name:
                raise ValueError(f"line {number}: core:end {name!r} does not close {open_name!r}")
            open_name = None
    if open_name is not None:
        raise ValueError(f"core block {open_name!r} is never closed")
    if not blocks:
        return None, "".join(full)
    return "\n".join("".join(block).strip("\r\n") + "\n" for block in blocks), "".join(full)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--print", action="store_true", dest="preview")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--autocompact-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-box-sync", action="store_true", help="Skip syncing routing policy to factory boxes")
    parser.add_argument("--reason", help="Logged reason for forcing installation from a stale revision")
    parser.add_argument(
        "--hook-dispatcher", choices=("on", "off"),
        help="on: fold the PreToolUse, PostToolUse, UserPromptSubmit and Stop hooks into hooks/hook-dispatch.py "
        "after the parity fixture passes against this home's guards; off: restore the per-hook config verbatim. "
        "Absent: keep the current state (a folded config stays folded, over the guards it was checked on).",
    )
    parser.add_argument(
        "--check-hook-paths", action="store_true",
        help="write nothing; exit 1 when a hook the live settings run (through the dispatcher registry when folded) "
        "names a release-scoped path, or a PreToolUse guard names a path that is missing",
    )
    return parser.parse_args()


def read_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def validate_markers(text: str, path: Path) -> None:
    starts = text.count(START)
    ends = text.count(END)
    if starts != ends or starts > 1:
        raise ValueError(f"malformed or duplicate managed routing markers in {path}")
    if starts and text.index(START) > text.index(END):
        raise ValueError(f"reversed managed routing markers in {path}")


def h2_span(text: str, headings: tuple[str, ...], path: Path) -> tuple[int, int] | None:
    matches: list[re.Match[str]] = []
    for heading in headings:
        pattern = re.compile(rf"(?m)^##[ \t]+{re.escape(heading)}[ \t]*$")
        matches.extend(pattern.finditer(text))
    if len(matches) > 1:
        raise ValueError(f"multiple legacy routing sections in {path}")
    if not matches:
        return None
    start = matches[0].start()
    next_h2 = re.search(r"(?m)^##[ \t]+", text[matches[0].end() :])
    end = matches[0].end() + (next_h2.start() if next_h2 else len(text[matches[0].end() :]))
    return start, end


def install_adapter(text: str, template: str, path: Path) -> str:
    validate_markers(text, path)
    if START in text:
        start = text.index(START)
        end = text.index(END, start) + len(END)
        updated = text[:start] + template.strip() + text[end:]
    else:
        span = h2_span(text, LEGACY_HEADINGS, path)
        if span:
            updated = text[: span[0]] + template.strip() + "\n\n" + text[span[1] :].lstrip("\n")
        elif text:
            updated = text.rstrip() + "\n\n" + template.strip() + "\n"
        else:
            updated = template.strip() + "\n"
    return updated if updated.endswith("\n") else updated + "\n"


def install_codex_doc(text: str, body: str, path: Path) -> str:
    text = uninstall_adapter(text, path)
    if text.count(CODEX_START) > 1 or text.count(CODEX_START) != text.count(CODEX_END):
        raise ValueError(f"malformed or duplicate Codex markers in {path}")
    block = CODEX_START + "\n" + body.strip() + "\n" + CODEX_END
    if CODEX_START in text:
        start = text.index(CODEX_START)
        end = text.index(CODEX_END, start) + len(CODEX_END)
        updated = text[:start] + block + text[end:]
    elif not text.strip() or text.strip() == body.strip():
        updated = block
    else:
        updated = text.rstrip() + "\n\n" + block
    return updated.rstrip() + "\n"


def uninstall_adapter(text: str, path: Path) -> str:
    validate_markers(text, path)
    if START not in text:
        span = h2_span(text, LEGACY_HEADINGS, path)
        if span:
            updated = text[: span[0]].rstrip() + "\n\n" + text[span[1] :].lstrip("\n")
            return updated.rstrip() + "\n" if updated.strip() else ""
        return text
    start = text.index(START)
    end = text.index(END, start) + len(END)
    updated = text[:start].rstrip() + "\n\n" + text[end:].lstrip("\n")
    return updated.rstrip() + "\n" if updated.strip() else ""


def is_owned_hook(command: Any) -> bool:
    return isinstance(command, str) and "ccdex-gpt-only.sh" in command




SEAT_GUARD_HOOK = {
    "type": "command",
    "command": '/usr/bin/python3 "$HOME/.claude/hooks/seat-guard.py"',
    "timeout": 5,
    "statusMessage": "Seat guard",
}


WORKFLOW_SEAT_GUARD_HOOK = {
    **SEAT_GUARD_HOOK,
    "command": SEAT_GUARD_HOOK["command"].replace("seat-guard", "workflow-seat-guard"),
    "statusMessage": "Workflow seat guard",
}


CLOSEOUT_HOOK = {
    "type": "command",
    "command": '/usr/bin/python3 "$HOME/.claude/hooks/subagent-closeout.py" 2>/dev/null || true',
    "timeout": 5,
    "statusMessage": "Dispatch closeout",
}


FOCUS_STEAL_HOOK = {
    "type": "command",
    "command": '/usr/bin/python3 "$HOME/.agents/policy/coding-agents/hooks/focus-steal-check.py" subagent-stop',
    "timeout": 15,
    "statusMessage": "Focus steal check",
}


FACTORY_CONTEXT_HOOK = {
    "type": "command",
    "command": '/usr/bin/python3 "$HOME/.claude/hooks/factory-context.py" 2>/dev/null || true',
    "timeout": 10,
    "statusMessage": "Factory context",
}


COMPACT_CHECKPOINT_HOOK = {
    "type": "command",
    "command": '/usr/bin/python3 "$HOME/.claude/hooks/compact-checkpoint.py"',
    "timeout": 10,
    "statusMessage": "Compact checkpoint",
}


def is_closeout_hook(command: Any) -> bool:
    return isinstance(command, str) and "hooks/subagent-closeout.py" in command


def is_seat_guard_hook(command: Any) -> bool:
    return isinstance(command, str) and "hooks/seat-guard.py" in command


def is_workflow_seat_guard_hook(command: Any) -> bool:
    return isinstance(command, str) and "hooks/workflow-seat-guard.py" in command


DANGEROUS_GUARD_HOOK = {"type": "command", "command": '"$HOME/.claude/hooks/dangerous-command-guard.sh"', "timeout": 10}
RAILWAY_GUARD_HOOK = {"type": "command", "command": 'bash "$HOME/.claude/hooks/railway-vars-guard.sh"', "timeout": 10}
KILL_SCOPE_GUARD_HOOK = {"type": "command", "command": '/usr/bin/python3 "$HOME/.claude/hooks/kill-scope-guard.py"',
                        "timeout": 10}

SETTINGS_HOOKS = (
    ("PreToolUse", "Agent", SEAT_GUARD_HOOK, False),
    ("PreToolUse", "Workflow", WORKFLOW_SEAT_GUARD_HOOK, False),
    ("PreToolUse", "Bash", RAILWAY_GUARD_HOOK, False),
    ("PreToolUse", "Bash", DANGEROUS_GUARD_HOOK, True),
    ("PreToolUse", "Bash", KILL_SCOPE_GUARD_HOOK, True),
    ("SubagentStop", None, CLOSEOUT_HOOK, False),
    ("SubagentStop", None, FOCUS_STEAL_HOOK, False),
    ("PostToolUse", "Workflow", {"type": "command",
        "command": '/usr/bin/python3 "$HOME/.claude/hooks/workflow-readback.py"',
        "timeout": 5}, False),
    ("SessionStart", "compact|resume", FACTORY_CONTEXT_HOOK, False),
    ("PreCompact", None, COMPACT_CHECKPOINT_HOOK, False),
)


def is_settings_hook(command: Any, hook: dict[str, Any]) -> bool:
    path = re.search(r'hooks/[^"\s]+', hook["command"])
    return isinstance(command, str) and path is not None and path.group() in command


def dispatcher_module(source: Path) -> Any:

    cached = getattr(dispatcher_module, "loaded", {})
    if source not in cached:
        import importlib.util

        spec = importlib.util.spec_from_file_location("hook_dispatch_source", source / "hooks" / "hook-dispatch.py")
        if spec is None or spec.loader is None:
            raise SystemExit(f"error: cannot load {source / 'hooks' / 'hook-dispatch.py'}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cached[source] = module
        dispatcher_module.loaded = cached  # type: ignore[attr-defined]
    return cached[source]


def rewrite_floor_commands(hooks: dict[str, Any], source: Path) -> list[str]:

    module = dispatcher_module(source)
    changed = []
    for group in hooks.get("PreToolUse") or []:
        for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
            command = hook.get("command") if isinstance(hook, dict) else None
            direct = module.legacy_floor(command) if isinstance(command, str) else None
            if direct:
                changed.append(f"{command[:60]!r} -> {direct}")
                hook["command"] = direct
    return changed


def opaque_floor_commands(hooks: dict[str, Any], source: Path) -> list[str]:

    module = dispatcher_module(source)
    found = []
    for group in hooks.get("PreToolUse") or []:
        for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
            command = hook.get("command") if isinstance(hook, dict) else None
            name = module.floor_name(command) if isinstance(command, str) else None
            if name and module.floor_exec(command, name) is None:
                found.append(f"{group.get('matcher') or '*'}: {command[:120]!r} (floor guard {name})")
    return found












DISPATCH_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
DISPATCH_SCRIPT = "hooks/hook-dispatch.py"
DISPATCH_REGISTRY = Path(".claude/hooks/dispatch/registry.json")
DISPATCH_PARITY = Path(".claude/hooks/dispatch/parity-last.json")



DISPATCH_INPROC = (
    "rm-dynamic-deny", "desktop-guard", "stash-guard", "herdr-shell-host-guard.py", "rm-cd-rewrite.py",
    "no-direct-merge.py", "box-offload-hook", "lane-bulletin-hook", "aside-regular-tabs.py",
    "workflow-relay-guard.py", "routing-pulse.py", "herdr-tab-autoname.py", "idle-agents.py",
)

DISPATCH_SKIP_MATCHERS = ("AskUserQuestion",)
DISPATCH_HOOK_KEYS = {"type", "command", "timeout", "statusMessage"}
SIMPLE_MATCHER = re.compile(r"[A-Za-z0-9_]+(?:\|[A-Za-z0-9_]+)*")
DEFAULT_HOOK_TIMEOUT = 600



NAME_LIST = re.compile(r"[a-zA-Z0-9_|, -]+")
TOOL_ALIASES = {"Task": "Agent", "KillShell": "TaskStop", "KillBash": "TaskStop", "ListPeers": "ListAgents",
                "Brief": "SendUserMessage", "ListMcpResources": "ListMcpResourcesTool",
                "ReadMcpResource": "ReadMcpResourceTool", "ReadMcpResourceDir": "ReadMcpResourceDirTool"}




DISPATCH_DROPPED = {"55c67a61ea18f3d6d58b572fe09f1699073e09ddbbc05b2d15ccf853937501d7": "global prettier --write",
                    "70e4747d8e5cf5206a535899bcc19eb6e3acff6e2325fce6932d0e014c59acaa": "load-governor",
                    "9185b387b7e583755f8534b4f1ef3cb3dbc08e860ba4270684920bd9bfd39e7e": "repeat-read-warn"}


def is_dispatch_hook(hook: Any) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and DISPATCH_SCRIPT in hook["command"]


def matcher_key(group: dict[str, Any]) -> str | None:

    matcher = group.get("matcher")
    if matcher in (None, "", "*"):
        return "*"
    if isinstance(matcher, str) and SIMPLE_MATCHER.fullmatch(matcher):
        return matcher
    return None


def tool_names(key: str) -> set[str]:
    return {TOOL_ALIASES.get(name.strip(), name.strip()) for name in re.split(r"[|,]", key) if name.strip()}


def keys_overlap(first: str, second: str) -> bool:
    return first == "*" or second == "*" or bool(tool_names(first) & tool_names(second))


def foldable_groups(groups: list[Any], event: str) -> list[bool]:

    ok = []
    for group in groups:
        key = matcher_key(group) if isinstance(group, dict) else None
        hooks = group.get("hooks") if isinstance(group, dict) else None
        good = (
            key is not None and key not in DISPATCH_SKIP_MATCHERS and isinstance(hooks, list) and bool(hooks)
            and set(group) <= {"matcher", "hooks"}
            and all(
                isinstance(hook, dict) and hook.get("type") == "command" and set(hook) <= DISPATCH_HOOK_KEYS
                and isinstance(hook.get("command"), str) and hook["command"].strip() and not is_dispatch_hook(hook)
                and (hook.get("timeout") is None or (isinstance(hook["timeout"], (int, float))
                                                     and not isinstance(hook["timeout"], bool) and hook["timeout"] > 0))
                and (hook.get("statusMessage") is None or isinstance(hook["statusMessage"], str))
                for hook in hooks
            )
        )
        ok.append(good)
    return unfold_shared(groups, ok)


def commands_of(group: Any) -> set[str]:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return {hook["command"] for hook in hooks if isinstance(hook, dict) and isinstance(hook.get("command"), str)} \
        if isinstance(hooks, list) else set()


def unfold_shared(groups: list[Any], eligible: list[bool]) -> list[bool]:

    eligible = list(eligible)
    changed = True
    while changed:
        changed = False
        for index, (group, good) in enumerate(zip(groups, eligible)):
            if not good:
                continue
            key, mine = matcher_key(group), commands_of(group)
            for other_index, other in enumerate(groups):
                if other_index == index or not mine & commands_of(other):
                    continue
                if eligible[other_index]:
                    clash = matcher_key(other) != key and keys_overlap(key, matcher_key(other))
                else:
                    clash = group_overlaps(other, key)
                if clash:
                    eligible[index] = False
                    changed = True
                    break
    return eligible


def group_overlaps(group: Any, key: str) -> bool:

    if not isinstance(group, dict):
        return True
    own = matcher_key(group)
    if own is not None:
        return keys_overlap(own, key)
    if key == "*" or not isinstance(group.get("matcher"), str):
        return True
    if NAME_LIST.fullmatch(group["matcher"]):
        return keys_overlap(group["matcher"], key)
    try:
        pattern = re.compile(group["matcher"])
    except re.error:
        return True
    names = tool_names(key)
    names |= {alias for alias, tool in TOOL_ALIASES.items() if tool in names}
    return any(pattern.search(name) for name in names)


def keep_config_order(groups: list[Any], eligible: list[bool]) -> list[bool]:

    eligible = list(eligible)
    changed = True
    while changed:
        changed = False
        first: dict[str, int] = {}
        for index, (group, good) in enumerate(zip(groups, eligible)):
            if not good:
                continue
            key = matcher_key(group)
            if key not in first:
                first[key] = index
                continue
            if any(
                group_overlaps(groups[between], key)
                and not (eligible[between] and matcher_key(groups[between]) == key)
                for between in range(first[key] + 1, index)
            ):
                eligible[index] = False
                changed = True
    return eligible


def drop_hooks(hooks: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:

    kept, dropped = json.loads(json.dumps(hooks)), []
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        out = []
        for group in groups:
            if not (isinstance(group, dict) and isinstance(group.get("hooks"), list)):
                out.append(group)
                continue
            left = []
            for hook in group["hooks"]:
                command = hook.get("command") if isinstance(hook, dict) else None
                name = DISPATCH_DROPPED.get(hashlib.sha256(command.encode("utf-8")).hexdigest()) \
                    if isinstance(command, str) else None
                if name:
                    dropped.append(f"{event} {group.get('matcher') or '*'}: {name}")
                else:
                    left.append(hook)
            if left:
                out.append({**group, "hooks": left})
            elif not group["hooks"]:
                out.append(group)
        kept[event] = out
    return kept, dropped


def registry_problem(registry: Any) -> str | None:

    if not isinstance(registry, dict):
        return "not a registry"
    per_hook, entries, dispatch = registry.get("per_hook"), registry.get("entries"), registry.get("dispatch")
    if not (isinstance(per_hook, dict) and isinstance(entries, dict) and isinstance(dispatch, dict)):
        return "per_hook, entries or dispatch is missing"

    if registry.get("rev") and registry_rev(entries) != registry["rev"]:
        return f"entries do not hash to rev {registry['rev']}"


    for event in dict.fromkeys(list(entries) + list(per_hook) + list(dispatch)):
        keyed = entries.get(event)
        groups = per_hook.get(event)
        folded = dispatch.get(event)
        if not isinstance(keyed, dict) or not isinstance(groups, list) or not isinstance(folded, list):
            return f"{event}: entries, per_hook or dispatch is not the right shape"



        derived: dict[str, list[Any]] = {}
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                return f"{event}: a per_hook group is not a group"
            if group in folded:
                continue
            key = matcher_key(group)
            if key is None or key not in keyed:
                return f"{event}: per_hook group {group.get('matcher')!r} is neither kept nor folded"
            for hook in group["hooks"]:
                if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
                    return f"{event}: a per_hook hook is not a command hook"
                if all(hook["command"] != seen["command"] for seen in derived.setdefault(key, [])):
                    derived[key].append(hook)
        for key, hooks in keyed.items():
            if not isinstance(hooks, list) or not hooks:
                return f"{event} '{key}': the entry is not a list of hooks"
            if hooks != derived.get(key):
                return f"{event} '{key}': the entry is not the per_hook groups folded into it"
        for group in folded:
            if not isinstance(group, dict):
                return f"{event}: a folded group is not a group"
            for hook in group.get("hooks") or []:
                if is_dispatch_hook(hook):
                    match = re.search(r"hook-dispatch\.py\"?\s+(\w+)\s+'([^']*)'", hook["command"])
                    if not match or match.group(1) != event or match.group(2) not in keyed:
                        return f"{event}: folded entry {hook['command'][:80]!r} names no entry"
            if not any(is_dispatch_hook(hook) for hook in group.get("hooks") or []) and group not in groups:
                return f"{event}: folded list keeps a group per_hook does not have"
    return None


def registry_rev(entries: dict[str, Any]) -> str:

    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def dispatch_group(
    event: str, key: str, matcher_field: Any, hooks: list[dict[str, Any]], rev: str
) -> dict[str, Any]:
    timeout = sum(hook.get("timeout") or DEFAULT_HOOK_TIMEOUT for hook in hooks) + 5
    entry: dict[str, Any] = {
        "type": "command",
        "command": f'python3 "$HOME/.claude/{DISPATCH_SCRIPT}" {event} \'{key}\' {rev}',
        "timeout": int(timeout) if float(timeout).is_integer() else timeout,
    }
    messages = list(dict.fromkeys(hook["statusMessage"] for hook in hooks if hook.get("statusMessage")))
    if messages:
        entry["statusMessage"] = ", ".join(messages)
    group: dict[str, Any] = {"hooks": [entry]}
    if matcher_field is not None or key != "*":
        group = {"matcher": key if key != "*" else matcher_field, "hooks": [entry]}
    return group


def fold_hooks(
    hooks: dict[str, Any], only: dict[str, list[Any]] | None = None, worth: Any = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:

    folded = json.loads(json.dumps(hooks))
    per_hook: dict[str, list[Any]] = {}
    entries: dict[str, dict[str, list[Any]]] = {}
    dispatch: dict[str, Any] = {}
    for event in DISPATCH_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not groups:
            continue
        eligible = foldable_groups(groups, event)
        if only is not None:
            allowed = only.get(event, [])
            eligible = [good and group in allowed for group, good in zip(groups, eligible)]
        eligible = keep_config_order(groups, eligible)
        if worth is not None:
            by_key: dict[str, list[Any]] = {}
            for group, good in zip(groups, eligible):
                if good:
                    by_key.setdefault(matcher_key(group), []).extend(group["hooks"])
            unworthy = {key for key, key_hooks in by_key.items() if not worth(key_hooks)}
            eligible = [good and matcher_key(group) not in unworthy for group, good in zip(groups, eligible)]

            eligible = keep_config_order(groups, eligible)
        while True:
            settled = keep_config_order(groups, unfold_shared(groups, eligible))
            if settled == eligible:
                break
            eligible = settled
        if not any(eligible):
            continue
        out: list[Any] = []
        placed: dict[str, dict[str, Any]] = {}
        event_entries: dict[str, list[Any]] = {}
        for group, good in zip(groups, eligible):
            if not good:
                out.append(json.loads(json.dumps(group)))
                continue
            key = matcher_key(group)
            event_entries.setdefault(key, [])
            for hook in group["hooks"]:
                if all(hook["command"] != seen["command"] for seen in event_entries[key]):
                    event_entries[key].append(json.loads(json.dumps(hook)))
            if key not in placed:
                placed[key] = {"matcher_field": group.get("matcher"), "index": len(out)}
                out.append(None)
        per_hook[event] = json.loads(json.dumps(groups))
        entries[event] = event_entries
        dispatch[event] = (out, placed)
    if not entries:
        return folded, None
    rev = registry_rev(entries)
    for event, (out, placed) in list(dispatch.items()):
        for key, spot in placed.items():
            out[spot["index"]] = dispatch_group(event, key, spot["matcher_field"], entries[event][key], rev)
        dispatch[event] = out
        folded[event] = out
    registry = {
        "schema": 1,
        "about": "hook-dispatch.py registry, written by agent-lb install-policy.py --hook-dispatcher on; "
        "per_hook is the config it replaced (restore: install-policy.py --hook-dispatcher off)",
        "rev": rev,
        "per_hook": per_hook,
        "dispatch": dispatch,
        "entries": entries,
        "inproc": list(DISPATCH_INPROC),
    }
    return folded, registry


def unfold_hooks(hooks: dict[str, Any], registry: dict[str, Any] | None) -> dict[str, Any]:

    unfolded = json.loads(json.dumps(hooks))
    for event in DISPATCH_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not any(
            isinstance(group, dict) and any(is_dispatch_hook(hook) for hook in group.get("hooks") or [])
            for group in groups
        ):
            continue



        split: list[Any] = []
        for group in groups:
            members = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(members, list) and len(members) > 1 and any(is_dispatch_hook(hook) for hook in members) \
                    and not all(is_dispatch_hook(hook) for hook in members):
                split.append({**group, "hooks": [hook for hook in members if is_dispatch_hook(hook)]})
                split.append({**group, "hooks": [hook for hook in members if not is_dispatch_hook(hook)]})
            else:
                split.append(group)
        groups = split
        if registry is None:
            raise ValueError(f"{event} hooks run through hook-dispatch.py but its registry is unreadable")
        if registry.get("dispatch", {}).get(event) == groups:
            unfolded[event] = json.loads(json.dumps(registry["per_hook"][event]))
            continue



        per_hook = registry.get("per_hook", {}).get(event, [])
        left = registry.get("dispatch", {}).get(event, [])
        plain = [group for group in groups
                 if not (isinstance(group, dict) and any(is_dispatch_hook(hook) for hook in group.get("hooks") or []))]
        keys = set()
        for group in groups:
            if group in plain:
                continue
            if len(group["hooks"]) != 1:
                raise ValueError(f"a {event} group mixes the dispatcher with other hooks; fix it by hand")
            match = re.search(r"hook-dispatch\.py\"?\s+(\w+)\s+'([^']*)'", group["hooks"][0]["command"])
            if not match or match.group(1) != event or match.group(2) not in registry.get("entries", {}).get(event, {}):
                raise ValueError(f"{event} dispatcher entry {group['hooks'][0]['command']!r} is not in the registry")
            keys.add(match.group(2))
        out: list[Any] = []
        for group in per_hook:
            if group in left:
                if group in plain:
                    plain.remove(group)
                    out.append(group)
            elif matcher_key(group) in keys:
                out.append(group)
        out.extend(plain)
        unfolded[event] = out
    return unfolded


DISPATCH_REV = re.compile(r"hook-dispatch\.py\"?\s+\w+\s+'[^']*'\s+([0-9a-f]{12})\b")
REV_FILE = re.compile(r"registry\.([0-9a-f]{12}|legacy)\.json")
REV_KEEP_SECONDS = 30 * 86400


def folded_revs(settings: dict[str, Any]) -> set[str | None]:

    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    revs: set[str | None] = set()
    for event in DISPATCH_EVENTS:
        for group in hooks.get(event) or []:
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if is_dispatch_hook(hook):
                    match = DISPATCH_REV.search(hook["command"])
                    revs.add(match.group(1) if match else None)
    return revs


def folded_rev(settings: dict[str, Any]) -> str | None:

    revs = folded_revs(settings)
    if len(revs) > 1:
        raise SystemExit(
            "error: the folded settings entries name different dispatcher registry revs (%s); no one registry can "
            "restore them, so nothing was written. Restore settings.json from the newest checkpoint under "
            "~/.agent-lb/config-checkpoints/coding-agents/ and run this again."
            % ", ".join(sorted(rev or "(none)" for rev in revs)))
    return next(iter(revs), None)


def read_registry(home: Path, rev: str | None = None) -> dict[str, Any] | None:

    base = home / DISPATCH_REGISTRY
    paths = [base, base.with_name("registry.json.bak")]
    if rev:
        paths.insert(0, base.with_name(f"registry.{rev}.json"))
    else:
        paths.append(base.with_name("registry.legacy.json"))
    for path in paths:
        try:
            registry = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (registry.get("rev") if isinstance(registry, dict) else None) != rev:
            continue
        if registry_problem(registry) is None:
            return registry
        print(f"WARNING hook dispatcher: {path} cannot restore the per-hook config ({registry_problem(registry)}); "
              "trying the next copy")
    return None


def is_folded(settings: dict[str, Any]) -> bool:
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    return any(
        is_dispatch_hook(hook)
        for event in DISPATCH_EVENTS for group in hooks.get(event) or [] if isinstance(group, dict)
        for hook in group.get("hooks") or []
    )


def dispatch_worth(source: Path, home: Path) -> Any:

    import importlib.util

    spec = importlib.util.spec_from_file_location("hook_dispatch", source / DISPATCH_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"error: cannot load {source / DISPATCH_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def worth(hooks: list[dict[str, Any]]) -> bool:
        if len({hook["command"] for hook in hooks}) > 1:
            return True
        saved = dict(os.environ)

        os.environ.pop("LC_ALL", None)
        os.environ.pop("LC_CTYPE", None)
        os.environ.update(HOME=str(home), LANG="en_US.UTF-8")
        try:
            plan = module.plan_hook(hooks[0], dict.fromkeys(DISPATCH_INPROC), script_pins(source))
        except Exception:
            return False
        finally:
            os.environ.clear()
            os.environ.update(saved)
        return plan.kind != "external"

    return worth






DERIVED_PINS = ("wide-scan-guard.sh", "agent-lb-bootout-guard.sh", "railway-vars-guard.sh", "link-cli-guard.sh",
                "dangerous-command-guard.sh")


def script_pins(source: Path) -> dict[str, str]:

    pins: dict[str, str] = {}
    for name in DERIVED_PINS:
        path = source / "hooks" / name
        if path.is_file():
            pins[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return pins


SCRIPT_WORD = re.compile(r'\s*(?:(?:/usr/bin/)?python3\s+)?("[^"]+"|[^\s"\'|;&<>]+)')


def managed_hooks(home: Path, source: Path) -> dict[Path, Path]:

    return {home / target: source / template for target, _owner, _marker, template in MANAGED_AGENTS
            if template.parts[0] == "hooks" and template != Path(DISPATCH_SCRIPT)}


def pin_inproc(registry: dict[str, Any], home: Path, source: Path) -> dict[str, Any]:

    pins: dict[str, str] = {}
    managed = {os.path.realpath(target): template for target, template in managed_hooks(home, source).items()}
    for event_entries in registry["entries"].values():
        for hooks in event_entries.values():
            for hook in hooks:
                match = SCRIPT_WORD.match(hook["command"])
                if not match:
                    continue
                word = match.group(1).strip('"')
                if word.startswith("$HOME/"):
                    word = str(home) + word[len("$HOME"):]
                elif word.startswith("~/"):
                    word = str(home) + word[1:]
                path = Path(os.path.realpath(word))
                path = managed.get(str(path), path)
                if path.name in DISPATCH_INPROC and path.is_file():
                    pins[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    pinned = dict(registry)
    pinned["inproc_sha"] = dict(sorted(pins.items()))
    pinned["script_sha"] = script_pins(source)
    pinned["dispatcher_sha256"] = hashlib.sha256((source / DISPATCH_SCRIPT).read_bytes()).hexdigest()
    return pinned


def run_parity(source: Path, home: Path, registry: dict[str, Any]) -> tuple[bool, str]:



    try:
        wake = subprocess.run(
            [sys.executable, str(source / "hooks" / "seat-run-wake-parity.py"),
             "--dispatcher", str(source / DISPATCH_SCRIPT)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"seat-run wake fixture did not run: {exc.__class__.__name__}"
    if wake.returncode != 0:
        return False, f"seat-run wake fixture failed: {wake.stderr.strip()[-400:]}"
    report = home / DISPATCH_PARITY
    report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hook-dispatch-stage-") as stage:
        staged = Path(stage) / "registry.json"
        staged.write_text(json.dumps(registry, indent=2) + "\n")
        try:
            result = subprocess.run(
                [sys.executable, str(source / "hooks" / "hook-dispatch-parity.py"), "--home", str(home),
                 "--registry", str(staged), "--dispatcher", str(source / DISPATCH_SCRIPT), "--out", str(report),
                 "--workdir", str(Path(tempfile.gettempdir()) / "hook-dispatch-parity"),
                 *[arg for target, template in managed_hooks(home, source).items()
                   for arg in ("--overlay", f"{target.relative_to(home)}={template}")],

                 "--lock-held", "--no-process-count"],
                capture_output=True, text=True, timeout=600, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"parity fixture did not run: {exc.__class__.__name__}"
    summary = (result.stdout.strip().splitlines() or [""])[-1]
    if result.returncode != 0:
        return False, f"{summary or result.stderr.strip()[-400:]} (exit {result.returncode}; report {report})"
    return True, f"{summary} (report {report})"


def resolve_sonnet(source: Path, home: Path) -> str:

    route = source.parent.parent / "clients" / "route"
    if not route.is_file():
        route = home / ".agent-lb" / "bin" / "route"
    try:
        result = subprocess.run(
            [sys.executable, str(route), "resolve", "sonnet-latest"],
            capture_output=True, text=True, timeout=60, check=False,
            env=os.environ | {"ROUTE_TABLE": str(source / "routing-table.json")},
        )
    except (OSError, subprocess.SubprocessError):
        return SONNET_MODEL
    model = result.stdout.strip() if result.returncode == 0 else ""
    return model if SONNET_ID.fullmatch(model) else SONNET_MODEL


def resolve_sol(source: Path, home: Path) -> str:

    route = source.parent.parent / "clients" / "route"
    if not route.is_file():
        route = home / ".agent-lb" / "bin" / "route"
    try:
        result = subprocess.run(
            [sys.executable, str(route), "resolve", "sol-latest"],
            capture_output=True, text=True, timeout=60, check=False,
            env=os.environ | {"ROUTE_TABLE": str(source / "routing-table.json")},
        )
    except (OSError, subprocess.SubprocessError):
        return SOL_MODEL
    model = result.stdout.strip() if result.returncode == 0 else ""
    return model if SOL_ID.fullmatch(model) else SOL_MODEL


RELAY_SEATS = frozenset((
    "codex-sol", "codex-sol-local", "codex-verifier", "codex-test-runner",
    "codex-test-runner-local", "gpt-implementer", "gpt-explorer", "sonnet-implementer",
    "verifier", "cursor-seat", "devin-seat", "muse-seat", "muse-seat-local",
    "computer-use", "computer-use-local", "host-relay",
))


def policy_snapshot(source: Path, home: Path) -> dict[str, Any]:
    route = source.parent.parent / "clients/route"
    if not route.is_file():
        route = home / ".agent-lb/bin/route"
    try:
        result = subprocess.run(
            [sys.executable, str(route), "policy", "snapshot", "--json"],
            capture_output=True, text=True, timeout=60, check=True,
            env=os.environ | {"ROUTE_TABLE": str(source / "routing-table.json")},
        )
        snapshot = json.loads(result.stdout)
        for key in ("policy_rev", "table_sha256", "seats", "standards"):
            if not snapshot.get(key):
                raise ValueError(f"missing {key}")
        return snapshot
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise SystemExit(f"error: route policy snapshot failed: {exc}") from exc


def snapshot_model(snapshot: dict[str, Any], model: str) -> str:
    if not model.endswith("-latest"):
        return model
    for key in ("resolved_models", "models", "aliases"):
        resolved = snapshot.get(key, {}).get(model)
        if isinstance(resolved, dict):
            resolved = resolved.get("resolved_model") or resolved.get("model") or resolved.get("resolved")
        if isinstance(resolved, str) and not resolved.endswith("-latest"):
            return resolved
    for seat in (snapshot.get("seats") or {}).values():
        if isinstance(seat, dict) and seat.get("model") == model and isinstance(seat.get("model_id"), str):
            return seat["model_id"]
    return model


def seat_template(text: str, name: str, snapshot: dict[str, Any]) -> str:
    seat = snapshot["seats"].get(name)
    if not isinstance(seat, dict):
        return text
    if name in RELAY_SEATS:
        model = snapshot_model(snapshot, snapshot["standards"]["relay"]["wrapper_model"])
        effort = "low"
    else:
        model = snapshot_model(snapshot, seat.get("resolved_model") or seat["model"])
        effort = seat["effort"]
    if "fable" in model.lower() and name != "fable-orchestrator":
        raise SystemExit(f"error: Fable is forbidden for {name}")
    parts = text.split("---", 2)
    for key, value in (("model", model), ("effort", effort)):
        pattern = rf"(?m)^{key}:.*$"
        if re.search(pattern, parts[1]):
            parts[1] = re.sub(pattern, f"{key}: {value}", parts[1])
        else:
            parts[1] += f"{key}: {value}\n"
    return "---".join(parts)


def check_policy(source: Path, home: Path, snapshot: dict[str, Any]) -> int:
    problems = []
    for relative, _, _, template in MANAGED_AGENTS:
        if template.parent == Path("agents"):
            wanted = seat_template((source / template).read_text(), template.stem, snapshot)
            for installed in (relative, POLICY_DIR / template):
                if read_text(home / installed) != wanted:
                    problems.append(str(installed))
    try:
        settings = json.loads(read_text(home / ".claude/settings.json"))
        background = snapshot_model(snapshot, snapshot["background_model"] if "background_model" in snapshot
                                    else snapshot["standards"]["background_model"])
        if (settings.get("effortLevel") != EFFORT_LEVEL or settings.get("model") != MODEL
                or settings.get("env", {}).get(HAIKU_ENV) != background
                or settings.get("env", {}).get(SONNET_ENV) != snapshot_model(snapshot, "sonnet-latest")):
            problems.append(".claude/settings.json")
        if json.loads(read_text(home / POLICY_SNAPSHOT)) != snapshot:
            problems.append(str(POLICY_SNAPSHOT))
    except (ValueError, KeyError, TypeError, AttributeError):
        problems.append("settings or snapshot missing/invalid")
    for problem in problems:
        print(f"policy drift: {problem}")
    return int(bool(problems))


def reconcile_codex_config(text: str, model: str) -> str:

    section = ""
    lines = []
    assignment = re.compile(
        r'^(\s*(model|extract_model|consolidation_model)\s*=\s*)([\"\'])(gpt-\d+(?:\.\d+)*-(?:sol|luna))(\3)(.*)$'
    )
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            section = line.strip()
        match = assignment.match(line.rstrip("\r\n"))
        if match and (match[2] != "model" or not section):
            start, end = match.span(4)
            line = line[:start] + model + line[end:]
        lines.append(line)
    return "".join(lines)


def internal_hook_commands(hooks: dict[str, Any], source: Path) -> None:

    dispatcher = dispatcher_module(source)
    for groups in hooks.values():
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook.get("command")
                if not isinstance(command, str):
                    continue



                before = command
                factory_bin = r'(?:\$HOME|/[^\s"\':;]*)/(?:(?:repos/)?factory|\.agent-rails/factory-runtime)/bin/'
                command = re.sub(factory_bin + r'agent-shims(?=[\s"\':;/]|$)',
                                 '$HOME/.local/share/desktop-guard/agent-shims', command)
                command = re.sub(factory_bin + r'(?=[^\s"\':;/]+(?:$|[\s"\';]))', '$HOME/.local/bin/', command)
                command = re.sub(factory_bin, '$HOME/.agent-rails/factory-runtime/bin/', command)
                from_factory = command != before and '/.local/bin/' in command
                command = re.sub(r'/[^\s"\':;]*/repos/personal/unblock/',
                                 '$HOME/.local/share/unblock-headless/', command)
                command = re.sub(r'/[^\s"\':;]*/repos/skill-stats/', '$HOME/.local/share/skill-stats/', command)

                match = re.fullmatch(r'((?:"[^"\n]+"|[^\s]+)\s+)?"((?:\$HOME)?/[^"\n]+)"(.*)', command)
                if match and (from_factory or any(path in command for path in (
                        '/.agent-rails/factory-runtime/', '/.local/share/unblock-headless/',
                        '/.local/share/skill-stats/'))) \
                        and not dispatcher.floor_name(command) \
                        and '# hook missing: fail open' not in command:
                    script = match[2]
                    command = (f'if [ -r "{script}" ]; then {command}; else '
                               f'printf \'%s\\n\' "hook warning: missing {script}; continuing" >&2; '
                               'fi # hook missing: fail open')
                hook["command"] = command


RELEASE_SCOPED = re.compile(r'factory-runtime|/factory/bin/|/repos/|/Volumes/')
HOOK_PATH = re.compile(r'(?:\$HOME|~|/)[^\s"\';|&)<>]*')


def hook_path_problems(home: Path) -> list[str]:

    settings = json.loads(read_text(home / ".claude/settings.json") or "{}")
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    if is_folded(settings):
        registry = read_registry(home, folded_rev(settings))
        if registry is None:
            return ["the dispatcher registry for these settings is missing or damaged"]
        hooks = unfold_hooks(hooks, registry)
    problems = []
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                command = hook.get("command")
                if not isinstance(command, str):
                    continue
                for word in HOOK_PATH.findall(command):
                    if word in ("/", "~"):
                        continue
                    if RELEASE_SCOPED.search(word):
                        problems.append(f"{event}: release-scoped path {word} in: {command}")
                    elif event == "PreToolUse" and not os.path.exists(
                            re.sub(r'^(?:\$HOME|~)(?=/)', str(home), word)):
                        problems.append(f"{event}: missing guard target {word} in: {command}")
    return problems


def reconcile_settings(settings: dict[str, Any], uninstall: bool, sonnet_model: str = SONNET_MODEL,
                       source: Path | None = None) -> dict[str, Any]:
    updated = json.loads(json.dumps(settings))
    hooks = updated.get("hooks", {})
    if not uninstall and source is not None and isinstance(hooks, dict):
        for line in rewrite_floor_commands(hooks, source):
            print(f"hook policy: floor guard registered as a direct exec: {line}")
    groups = hooks.get("PreToolUse", [])
    cleaned: list[dict[str, Any]] = []
    for group in groups:
        group_copy = dict(group)
        remaining = [hook for hook in group.get("hooks", []) if not is_owned_hook(hook.get("command"))]
        if remaining:
            group_copy["hooks"] = remaining
            cleaned.append(group_copy)
    if uninstall:
        for event in dict.fromkeys(entry[0] for entry in SETTINGS_HOOKS):
            managed = [entry[2] for entry in SETTINGS_HOOKS if entry[0] == event]
            kept = [
                {**group, "hooks": [hook for hook in group.get("hooks", [])
                                   if not any(is_settings_hook(hook.get("command"), owned) for owned in managed)]}
                for group in (cleaned if event == "PreToolUse" else hooks.get(event, []))
            ]
            kept = [group for group in kept if group["hooks"]]
            if event == "PreToolUse":
                cleaned = kept
            elif kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
    if groups:
        if cleaned:
            hooks["PreToolUse"] = cleaned
        else:
            hooks.pop("PreToolUse", None)
        if not hooks:
            updated.pop("hooks", None)
    env = updated.get("env")
    if uninstall:
        if isinstance(env, dict) and isinstance(env.get(SONNET_ENV), str) and SONNET_ID.fullmatch(env[SONNET_ENV]):
            env.pop(SONNET_ENV)
            if not env:
                updated.pop("env", None)
    else:
        if "env" not in updated:
            env = updated["env"] = {}
        elif not isinstance(env, dict):
            raise SystemExit("error: settings.json env is not a JSON object; fix it before installing the policy")
        env[SONNET_ENV] = sonnet_model
    if not uninstall:
        updated["model"] = MODEL
        updated["effortLevel"] = EFFORT_LEVEL
        for event, matcher, hook, needs_source in SETTINGS_HOOKS:
            if needs_source and source is None:
                continue
            event_groups = updated.setdefault("hooks", {}).setdefault(event, [])
            matchers = ("Bash", "*") if matcher == "Bash" else (matcher,)
            if any(
                is_settings_hook(existing.get("command"), hook)
                for group in event_groups
                if matcher is None or group.get("matcher") in matchers
                for existing in group.get("hooks", [])
            ):
                continue
            if matcher in ("Agent", "Workflow"):
                group = next((group for group in event_groups if group.get("matcher") == matcher), None)
                if group is not None:
                    group.setdefault("hooks", []).insert(0, dict(hook))
                    continue
            group = {"hooks": [dict(hook)]}
            if matcher is not None:
                group["matcher"] = matcher
            if hook is RAILWAY_GUARD_HOOK and source is None:
                workflow_index = next((i for i, existing in enumerate(event_groups)
                                       if existing.get("matcher") == "Workflow"), len(event_groups))
                event_groups.insert(workflow_index, group)
            else:
                event_groups.append(group)
    if not uninstall and source is not None:
        internal_hook_commands(updated.get("hooks", {}), source)
    dedupe_hooks(updated.get("hooks"))
    return updated


def dedupe_hooks(hooks: Any) -> None:

    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        seen: set[tuple[Any, str]] = set()
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept.append(group)
                continue
            items = []
            for hook in group["hooks"]:
                command = hook.get("command") if isinstance(hook, dict) else None
                key = (group.get("matcher"), command)
                if isinstance(command, str) and key in seen:
                    continue
                if isinstance(command, str):
                    seen.add(key)
                items.append(hook)
            if items:
                kept.append({**group, "hooks": items})
        hooks[event] = kept


def desired_routing_table(source_path: Path, live_path: Path) -> str:
    table = json.loads(source_path.read_text())
    live_text = read_text(live_path)
    if live_text:
        try:
            live = json.loads(live_text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"error: invalid JSON in {live_path}: {exc}") from exc
        if isinstance(live, dict) and "overrides" in live:
            table["overrides"] = [entry for entry in live["overrides"]
                                  if isinstance(entry, dict) and entry.get("class") in table["classes"]]
    return json.dumps(table, indent=2) + "\n"


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o755 if path.suffix == ".sh" else (path.stat().st_mode & 0o777 if path.exists() else 0o644)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def policy_box_hosts(home: Path) -> list[str]:
    registry = Path(os.environ.get("FACTORY_HOME") or home / ".agent-rails/factory").expanduser()
    defaults = home / ".agent-rails/factory-runtime/fleet/config"

    def load(name: str, override: str | None = None) -> Any:
        path = Path(override).expanduser() if override else registry / name
        if not path.exists() and not override:
            path = defaults / name
        return json.loads(path.read_text())

    machines = load("machines.json", os.environ.get("FACTORY_MACHINES"))
    boxes = load("boxes.json", os.environ.get("FLEET_BOXES_FILE"))
    boxes = boxes.get("boxes", []) if isinstance(boxes, dict) else boxes
    disabled = {row.get("name") for row in [*machines, *boxes]
                if row.get("enabled") is False or row.get("kind") == "slot"}
    known = {name for row in machines for name in [row["name"], *row.get("aliases", [])]}
    hosts = [row["name"] for row in machines
             if row.get("job_host") is True and not str(row.get("os", "")).lower().startswith("macos")
             and row["name"] not in disabled and row.get("ssh", row["name"]) not in disabled]
    hosts.extend(row["name"] for row in boxes
                 if row.get("kind") == "plain" and row.get("enabled") is True and row["name"] not in known)
    return list(dict.fromkeys(hosts))


def sync_policy_boxes(home: Path) -> None:
    try:
        hosts = policy_box_hosts(home)
        table = home / ROUTING_TABLE
        expected = {"policy_rev": json.loads(table.read_text())["policy_rev"],
                    "table_sha256": hashlib.sha256(table.read_bytes()).hexdigest()}
        files = [home / ".agent-lb/bin/route", home / ".agent-lb/bin/seat", table]
        if not all(path.is_file() for path in files):
            raise ValueError("installed route, seat or routing table is missing")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"WARNING: box policy sync unavailable ({type(exc).__name__})", file=sys.stderr)
        return
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    ssh_config = home / ".agent-rails/factory/ssh/hosts.conf"
    if ssh_config.is_file():
        ssh += ["-F", str(ssh_config)]
    verify = ('ROUTE_TABLE="$HOME/.agent-lb/managed/coding-agents/routing-table.json" '
              'python3 "$HOME/.agent-lb/bin/route" policy snapshot --json')
    for host in hosts:
        try:
            if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
                raise ValueError("invalid box name")
            subprocess.run([*ssh, host, 'mkdir -p "$HOME/.agent-lb/bin" '
                            '"$HOME/.agent-lb/managed/coding-agents"'],
                           capture_output=True, text=True, check=True, timeout=30)
            for sources, destination in ((files[:2], ".agent-lb/bin/"),
                                         (files[2:], ".agent-lb/managed/coding-agents/")):
                subprocess.run(["rsync", "-az", "-e", shlex.join(ssh),
                                *(str(path) for path in sources), f"{host}:{destination}"],
                               capture_output=True, text=True, check=True, timeout=60)
            result = subprocess.run([*ssh, host, verify], capture_output=True, text=True,
                                    check=True, timeout=60)
            actual = json.loads(result.stdout)
            if any(actual.get(key) != value for key, value in expected.items()):
                raise ValueError("policy_rev or table_sha256 mismatch")
            print(f"box policy synced {host}: policy_rev={expected['policy_rev']} "
                  f"table_sha256={expected['table_sha256']}")
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            print(f"WARNING: box policy sync failed for {host} ({type(exc).__name__}); "
                  "local install succeeded", file=sys.stderr)


def checkpoint_changes(home, changes, replace_policy_link=False, policy_link_target=""):
    checkpoint_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    checkpoint = home / ".agent-lb" / "config-checkpoints" / "coding-agents" / checkpoint_name
    checkpoint.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, str] = {}
    for path in changes:
        if path.exists():
            relative = path.relative_to(home)
            destination = checkpoint / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            manifest[str(relative)] = "copied"
        else:
            manifest[str(path.relative_to(home))] = "absent"
    if replace_policy_link:
        manifest[str(POLICY_DIR)] = f"symlink -> {policy_link_target}"

        shutil.copytree(
            home / POLICY_DIR,
            checkpoint / "policy-link-target",
            ignore=shutil.ignore_patterns("__pycache__"),
            symlinks=True,
        )
    (checkpoint / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if replace_policy_link:
        policy_dir = home / POLICY_DIR
        policy_dir.unlink()


        shutil.copytree(checkpoint / "policy-link-target", policy_dir, symlinks=True)
        print(f"replaced symlink {policy_dir} -> {policy_link_target} with an installed copy")
    print(f"checkpoint {checkpoint}")



def autocompact_text(text, window):
    decoder = json.JSONDecoder()
    index = text.index("{") + 1
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if text[index] == "}":
            prefix = "," if json.loads(text) else ""
            return text[:index].rstrip() + prefix + '\n  "autoCompactWindow": ' + str(window) + "\n" + text[index:]
        key, index = decoder.raw_decode(text, index)
        while text[index].isspace() or text[index] == ":":
            index += 1
        start = index
        _, index = decoder.raw_decode(text, index)
        if key == "autoCompactWindow":
            return text[:start] + str(window) + text[index:]
        while text[index].isspace():
            index += 1
        if text[index] == ",":
            index += 1


def main() -> int:
    args = parse_args()
    if args.check_hook_paths:
        problems = hook_path_problems(args.home)
        for line in problems:
            print(line)
        print("hook paths: ok" if not problems else f"hook paths: {len(problems)} problem(s)")
        return 1 if problems else 0
    source = Path(__file__).resolve().parent
    snapshot = None if args.uninstall else policy_snapshot(source, args.home)
    if args.check:
        return check_policy(source, args.home, snapshot)
    if not (args.preview or args.uninstall):
        verify_install_revision(source, args.force, args.reason)
    claude_path = args.home / ".claude" / "CLAUDE.md"
    codex_path = args.home / ".codex" / "AGENTS.md"
    template = (source / "claude-adapter.md").read_text()
    originals = {path: read_text(path) for path in (claude_path, codex_path)}
    settings_path = args.home / ".claude" / "settings.json"
    settings_text = read_text(settings_path)
    try:
        settings = json.loads(settings_text) if settings_text else {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: invalid JSON in {settings_path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise SystemExit(f"error: expected a JSON object in {settings_path}")

    if args.autocompact_only:
        if args.uninstall:
            raise SystemExit("error: --autocompact-only cannot uninstall")
        window = snapshot["autocompact_window"]["tab"]
        if type(window) is not int or window <= 0:
            raise SystemExit("error: invalid autocompact tab window")
        desired = autocompact_text(settings_text or "{}\n", window)
        if desired != settings_text:
            if args.preview:
                print(f"would set autoCompactWindow={window} in {settings_path}")
            else:
                if read_text(settings_path) != settings_text:
                    raise SystemExit("error: settings changed during install")
                checkpoint_changes(args.home, {settings_path: desired})
                write_atomic(settings_path, desired)
        print(f"autoCompactWindow={window}")
        return 0

    disk_settings = settings
    registry_path = args.home / DISPATCH_REGISTRY
    folded_now = is_folded(settings)
    old_registry = read_registry(args.home, folded_rev(settings)) if folded_now else None
    if folded_now:
        try:
            settings = {**settings, "hooks": unfold_hooks(settings["hooks"], old_registry)}
        except ValueError as exc:
            raise SystemExit(
                f"error: {exc}. The dispatcher refuses PreToolUse calls until this is fixed: restore {registry_path} "
                "from the newest checkpoint under ~/.agent-lb/config-checkpoints/coding-agents/"
            ) from exc

    try:
        desired_docs = {
            claude_path: (
                uninstall_adapter(originals[claude_path], claude_path)
                if args.uninstall
                else install_adapter(originals[claude_path], template, claude_path)
            ),

            codex_path: (
                uninstall_adapter(originals[codex_path], codex_path)
                if args.uninstall
                else install_codex_doc(originals[codex_path], (source / "codex-AGENTS.md").read_text(), codex_path)
            ),
        }
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    sonnet_model = SONNET_MODEL if args.uninstall else snapshot_model(snapshot, "sonnet-latest")
    desired_settings = reconcile_settings(settings, args.uninstall, sonnet_model, source)
    if not args.uninstall:
        desired_settings["autoCompactWindow"] = snapshot["autocompact_window"]["tab"]
    if args.uninstall:
        if isinstance(desired_settings.get("env"), dict):
            desired_settings["env"].pop(HAIKU_ENV, None)
            if not desired_settings["env"]:
                desired_settings.pop("env")
    else:
        background = snapshot.get("background_model") or snapshot["standards"]["background_model"]
        desired_settings["env"][HAIKU_ENV] = snapshot_model(snapshot, background)
    mode = "off" if args.uninstall else (args.hook_dispatcher or ("sticky" if folded_now else "off"))
    if mode != "off" and isinstance(desired_settings.get("hooks"), dict):
        opaque = opaque_floor_commands(desired_settings["hooks"], source)
        if opaque:
            raise SystemExit(
                "error: hook dispatcher not installed, nothing written: a floor guard must be registered as a direct "
                "exec of its own script (`[python3|bash] <script> [args]`, S44), and the dispatcher refuses every call "
                "these would guard: " + "; ".join(opaque))
    new_registry: dict[str, Any] | None = None
    keep_fold = False
    if mode != "off" and isinstance(desired_settings.get("hooks"), dict):
        kept_hooks, dropped = drop_hooks(desired_settings["hooks"])
        for name in dropped:
            print(f"hook dispatcher: dropped {name} at this fold")
        desired_settings = {**desired_settings, "hooks": kept_hooks}
        only = None
        if mode == "sticky" and old_registry is not None:



            only = {}
            for event, groups in old_registry["per_hook"].items():
                folded_groups = json.loads(json.dumps(
                    [group for group in groups if group not in old_registry.get("dispatch", {}).get(event, [])]))
                rewrite_floor_commands({"PreToolUse": folded_groups} if event == "PreToolUse" else {}, source)
                only[event] = folded_groups
        folded_hooks, candidate = fold_hooks(desired_settings["hooks"], only, dispatch_worth(source, args.home))
        if candidate is not None:
            candidate = pin_inproc(candidate, args.home, source)
            if mode == "sticky" and old_registry is not None:
                candidate["parity"] = old_registry.get("parity")
            unchanged = mode == "sticky" and old_registry is not None and all(
                candidate.get(field) == old_registry.get(field)
                for field in ("inproc_sha", "dispatcher_sha256", "script_sha")
            )
            if args.preview:
                print(f"would fold hooks into the dispatcher ({'refold' if unchanged else 'after the parity fixture'})")
                new_registry = candidate
            elif unchanged:
                new_registry = candidate
            else:
                ok, summary = run_parity(source, args.home, candidate)
                if not ok and mode == "sticky":

                    print(f"hook dispatcher: parity fixture failed ({summary}); running it once more")
                    ok, summary = run_parity(source, args.home, candidate)
                if ok:
                    candidate["parity"] = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                           "summary": summary}
                    new_registry = candidate
                    print(f"hook dispatcher parity passed: {summary}")
                elif mode == "on":
                    raise SystemExit(f"error: hook dispatcher not installed; parity fixture failed: {summary}")
                elif folded_now and old_registry is not None:



                    keep_fold = True
                    print(f"WARNING hook dispatcher: a guard or the dispatcher changed and the parity fixture failed "
                          f"twice ({summary}); keeping the installed fold (rev {old_registry.get('rev')}) unchanged")
                else:
                    print(f"WARNING hook dispatcher: a guard or the dispatcher changed and the parity fixture failed "
                          f"({summary}); restoring the per-hook config")
            if new_registry is not None:
                desired_settings = {**desired_settings, "hooks": folded_hooks}
            elif keep_fold:
                hooks_now = dict(desired_settings["hooks"])
                for event in DISPATCH_EVENTS:
                    if event in (disk_settings.get("hooks") or {}):
                        hooks_now[event] = disk_settings["hooks"][event]
                    else:
                        hooks_now.pop(event, None)
                desired_settings = {**desired_settings, "hooks": hooks_now}
    desired_settings_text = json.dumps(desired_settings, indent=2, ensure_ascii=False) + "\n"
    changes: dict[Path, str | None] = {
        path: desired for path, desired in desired_docs.items() if desired != originals[path]
    }
    models_path = args.home / ".claude/rules/models.md"
    models_text = models_path.read_bytes().decode("utf-8") if models_path.exists() else ""
    full_path = args.home / MODELS_FULL
    full_disk = full_path.read_bytes().decode("utf-8") if full_path.exists() else ""
    if args.uninstall:
        if models_text.startswith(MODELS_MARKER):
            changes[models_path] = None
        if full_disk.startswith(MODELS_MARKER):
            changes[full_path] = None
    else:
        routing_bytes = (source / "ROUTING.md").read_bytes()
        try:
            core_text, full_text = split_core(routing_bytes.decode("utf-8"))
        except ValueError as exc:
            raise SystemExit(f"error: ROUTING.md core markers: {exc}") from exc
        workspace_path = source / "WORKSPACE.md"
        workspace_bytes = workspace_path.read_bytes() if workspace_path.is_file() else b""
        rules_rev = hashlib.sha256(routing_bytes + workspace_bytes).hexdigest()[:12]
        header = (
            f'{MODELS_MARKER} from config/coding-agents/ROUTING.md (rules_rev {rules_rev}). '
            'Do not edit: the next install overwrites local edits. Change the rule in agent-lb, '
            'or ask `factory ask "<question>" --json`. History: ~/.claude/ledgers/models-ledger.md. -->'
        )
        desired_models = header + "\n\n" + (full_text if core_text is None else core_text)
        desired_full = header + "\n\n" + full_text
        if full_path.exists() and not full_disk.startswith(MODELS_MARKER):
            print(f"SKIP {full_path}: hand-kept file, not overwritten")
        elif desired_full != full_disk:
            changes[full_path] = desired_full
        if models_path.exists() and not models_text.startswith(MODELS_MARKER):
            date = datetime.now(timezone.utc).strftime("%Y%m%d")
            backup = args.home / f".claude/ledgers/models-md-handkept-{date}.md"
            if not backup.exists():
                changes[backup] = models_text
        if desired_models != models_text:
            changes[models_path] = desired_models

    if desired_settings != disk_settings:
        changes[settings_path] = desired_settings_text
    if not args.uninstall and registry_path.parent.is_dir():


        for path in registry_path.parent.glob("registry*.json"):
            legacy = json.loads(path.read_text())
            if isinstance(legacy, dict) and legacy.get("rev") and isinstance(legacy.get("entries"), dict) \
                    and registry_rev(legacy["entries"]) == legacy["rev"]:


                continue
            before = json.dumps(legacy, sort_keys=True)

            def relocate(value: Any) -> None:
                if isinstance(value, dict):
                    if isinstance(value.get("command"), str):
                        internal_hook_commands({"legacy": [{"hooks": [value]}]}, source)
                    for child in value.values():
                        relocate(child)
                elif isinstance(value, list):
                    for child in value:
                        relocate(child)

            relocate(legacy)
            if json.dumps(legacy, sort_keys=True) != before:
                changes[path] = json.dumps(legacy, indent=2, ensure_ascii=False) + "\n"
    backup_path = registry_path.with_name("registry.json.bak")
    rev_path = None
    legacy_path = registry_path.with_name("registry.legacy.json")
    if new_registry is not None:
        registry_text = json.dumps(new_registry, indent=2, ensure_ascii=False) + "\n"
        rev_path = registry_path.with_name(f"registry.{new_registry['rev']}.json")
        for path in (rev_path, backup_path, registry_path):
            if read_text(path) != registry_text:
                changes[path] = registry_text
    elif not keep_fold:
        for path in (registry_path, backup_path):
            if path.exists():
                changes[path] = None
    if folded_now and old_registry is not None and folded_rev(disk_settings) is None:



        legacy_text = json.dumps(old_registry, indent=2, ensure_ascii=False) + "\n"
        if read_text(legacy_path) != legacy_text:
            changes[legacy_path] = legacy_text
    in_use = {f"registry.{rev}.json" for rev in (folded_rev(desired_settings), folded_rev(disk_settings)) if rev}
    if registry_path.parent.is_dir():
        for path in registry_path.parent.iterdir():
            if REV_FILE.fullmatch(path.name) and path != rev_path and path not in changes and \
                    path.name not in in_use and time.time() - path.stat().st_mtime > REV_KEEP_SECONDS:
                changes[path] = None
    if not args.uninstall:
        codex_config = args.home / ".codex" / "config.toml"
        if codex_config.is_file():
            codex_config = codex_config.resolve()
            try:
                codex_config.relative_to(args.home.resolve())
                config_text = codex_config.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                print("SKIP Codex config rewrite: config.toml is not UTF-8")
            except ValueError:
                print("SKIP Codex config rewrite: config.toml target is outside --home")
            else:
                desired_config = reconcile_codex_config(config_text, resolve_sol(source, args.home))
                if desired_config != config_text:
                    changes[codex_config] = desired_config
    preserved_agents: list[tuple[str, Path]] = []
    replace_policy_link = False
    policy_link_target = ""
    for relative_path, owner_relative_path, owner_marker, template_relative_path in MANAGED_AGENTS:
        agent_path = args.home / relative_path
        owner_path = args.home / owner_relative_path
        agent_template = (source / template_relative_path).read_text()
        if args.uninstall and (args.home / POLICY_SNAPSHOT).is_file():
            installed_snapshot = json.loads((args.home / POLICY_SNAPSHOT).read_text())
            if template_relative_path.parent == Path("agents"):
                agent_template = seat_template(agent_template, template_relative_path.stem, installed_snapshot)
        if snapshot is not None and template_relative_path.parent == Path("agents"):
            agent_template = seat_template(agent_template, template_relative_path.stem, snapshot)
        agent_text = read_text(agent_path)
        agent_owned = read_text(owner_path) == owner_marker
        if args.uninstall:
            if agent_owned and relative_path in ADOPTED_KEEP:
                changes[owner_path] = None
                if agent_path.exists():
                    preserved_agents.append(("adopted guard, still registered,", agent_path))
            elif agent_owned:
                changes[owner_path] = None
                if agent_text == agent_template:
                    changes[agent_path] = None
                elif agent_path.exists():
                    preserved_agents.append(("customized", agent_path))
            elif agent_path.exists():
                preserved_agents.append(("unmanaged", agent_path))
        else:
            if keep_fold and template_relative_path in (Path(DISPATCH_SCRIPT), Path("hooks/hook-dispatch-parity.py")):


                if agent_text != agent_template:
                    print(f"hook dispatcher: keeping the installed {relative_path.name}; "
                          "the new one failed the fixture")
                continue
            if agent_text != agent_template:
                changes[agent_path] = agent_template
            if not agent_owned:
                changes[owner_path] = owner_marker

    if args.uninstall:
        if (args.home / POLICY_SNAPSHOT).exists():
            changes[args.home / POLICY_SNAPSHOT] = None
    else:
        wanted_snapshot = json.dumps(snapshot, indent=2) + "\n"
        if read_text(args.home / POLICY_SNAPSHOT) != wanted_snapshot:
            changes[args.home / POLICY_SNAPSHOT] = wanted_snapshot

        for name, template in (("route.sh", "jev-route.sh"),
                               ("health-check.sh", "jev-health-check.sh"),
                               ("jev-health.py", "jev-health.py")):
            target = args.home / ".jev/hooks" / name
            wanted = (source / "hooks" / template).read_text()
            if read_text(target) != wanted or (target.suffix == ".sh" and target.stat().st_mode & 0o777 != 0o755):
                changes[target] = wanted
        luna_owner = args.home / ".agent-lb/managed/coding-agents/luna-implementer"
        if read_text(luna_owner) == "agent-lb:luna-implementer:v1\n":
            for relative in (
                Path(".claude/agents/luna-implementer.md"),
                Path(".agent-lb/managed/coding-agents/luna-implementer"),
                POLICY_DIR / "agents/luna-implementer.md",
            ):
                path = args.home / relative
                if path.is_file() and path.resolve().parent != source / "agents":
                    changes[path] = None
        for name in RETIRED_AGENTS:
            for relative in (
                Path(".claude/agents") / f"{name}.md",
                Path(".agent-lb/managed/coding-agents") / name,
                POLICY_DIR / "agents" / f"{name}.md",
            ):
                if (args.home / relative).is_file() and (args.home / relative).resolve().parent != source / "agents":
                    changes[args.home / relative] = None
        table_path = args.home / ROUTING_TABLE
        desired_table = desired_routing_table(source / "routing-table.json", table_path)
        if desired_table != read_text(table_path):
            changes[table_path] = desired_table
        owner_path, owner_marker = ROUTING_TABLE_OWNER
        if read_text(args.home / owner_path) != owner_marker:
            changes[args.home / owner_path] = owner_marker
        policy_dir = args.home / POLICY_DIR

        route_source = source.parent.parent / "clients" / "route"
        if route_source.is_file():
            route_path = args.home / ".agent-lb" / "bin" / "route"
            if read_text(route_path) != route_source.read_text():
                changes[route_path] = route_source.read_text()
            route_owner = args.home / ".agent-lb" / "managed" / "coding-agents" / "route-cli"
            if read_text(route_owner) != "agent-lb:route-cli:v1\n":
                changes[route_owner] = "agent-lb:route-cli:v1\n"

        seat_source = source.parent.parent / "clients" / "seat"
        if seat_source.is_file():
            seat_path = args.home / ".agent-lb" / "bin" / "seat"
            if read_text(seat_path) != seat_source.read_text():
                changes[seat_path] = seat_source.read_text()


        decider_source = source.parent.parent / "clients" / "open-factory" / "open_factory" / "decider.json"
        if decider_source.is_file():
            decider_path = args.home / ROUTING_TABLE.with_name("decider.json")
            if read_text(decider_path) != decider_source.read_text():
                changes[decider_path] = decider_source.read_text()


        replace_policy_link = policy_dir.is_symlink()
        if replace_policy_link:
            policy_link_target = os.readlink(policy_dir)
        if replace_policy_link or policy_dir.resolve() != source:
            policy_sources = [Path(name) for name in POLICY_FILES]
            policy_sources += [path.relative_to(source) for path in sorted((source / "agents").glob("*.md"))]

            policy_sources += [path.relative_to(source) for pattern in ("*.py", "*.sh")
                               for path in sorted((source / "hooks").glob(pattern))]
            policy_sources += [Path("hooks/local-seat-bin/desktop-guard-focus")]
            for relative in policy_sources:
                wanted = (source / relative).read_text()
                if relative.parent == Path("agents"):
                    wanted = seat_template(wanted, relative.stem, snapshot)


                if replace_policy_link or read_text(policy_dir / relative) != wanted:
                    changes[policy_dir / relative] = wanted

    action = (
        "remove managed routing configuration from" if args.uninstall else "converge managed routing configuration in"
    )
    if args.preview:
        for path in changes:
            print(f"would {action} {path}")
        for reason, path in preserved_agents:
            print(f"would preserve {reason} {path}")
        if not changes:
            print("managed routing configuration already converged")
        return 0
    if not changes:
        for reason, path in preserved_agents:
            print(f"preserved {reason} {path}")
        print("managed routing configuration already converged")
        return 0



    if read_text(settings_path) != settings_text:
        raise SystemExit(f"error: {settings_path} changed while this install ran; nothing written, run it again")
    checkpoint_changes(args.home, changes, replace_policy_link, policy_link_target)

    analytics = args.home / "repos/skill-stats"
    installed_analytics = args.home / ".local/share/skill-stats"
    if analytics.is_dir() and (analytics / "dist/cli.js").is_file():
        for name in ("dist", "node_modules"):
            if (analytics / name).is_dir():
                shutil.copytree(analytics / name, installed_analytics / name,
                                dirs_exist_ok=True, symlinks=False)
        shutil.copy2(analytics / "package.json", installed_analytics / "package.json")




    first_files = {args.home / ".claude" / DISPATCH_SCRIPT, legacy_path} | ({rev_path} if rev_path else set())
    latest_files = {registry_path, backup_path}

    guard_files = set(managed_hooks(args.home, source)) - {args.home / ".claude" / DISPATCH_SCRIPT}

    def write_order(item: tuple[Path, str | None]) -> int:
        path, content = item
        if content is None:
            return 3
        if path in guard_files:
            return -1
        if path in first_files:
            return 0
        return 2 if path in latest_files else 1

    for path, content in sorted(changes.items(), key=write_order):
        if content is None:
            path.unlink()
            print(f"removed {path}")
        else:
            write_atomic(path, content)
            if path.suffix in (".py", ".sh") or path.name in ("verify-routing", "route", "seat"):
                os.chmod(path, path.stat().st_mode | 0o111)
            print(f"updated {path}")
    for reason, path in preserved_agents:
        print(f"preserved {reason} {path}")
    return 0


def same_policy_tree(bundle: Path, checkout: Path) -> bool:

    def files(root: Path) -> dict[Path, bytes]:
        return {path.relative_to(root): path.read_bytes() for path in sorted(root.rglob("*"))
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    try:
        return checkout.is_dir() and files(bundle) == files(checkout)
    except OSError:
        return False


def verify_install_revision(source: Path, force: bool = False, reason: str | None = None) -> None:

    repo = source.parent.parent
    if not (repo / ".git").exists():
        origin_repo = os.environ.get("AGENT_LB_POLICY_SOURCE_REPO")
        if not origin_repo or not same_policy_tree(source, Path(origin_repo) / "config/coding-agents"):
            raise SystemExit("error: installer needs a git worktree; rebase onto origin/main")
        repo = Path(origin_repo)
        if not (repo / ".git").exists():
            raise SystemExit("error: installer needs a git worktree; rebase onto origin/main")
    def git(*words):
        return subprocess.run(["git", "-C", str(repo), *words], capture_output=True, text=True, timeout=60)
    fetched = git("fetch", "origin", "main")
    if fetched.returncode:
        raise SystemExit("error: could not fetch origin/main; refusing policy installation")
    head = git("rev-parse", "HEAD").stdout.strip()
    main_sha = git("rev-parse", "origin/main").stdout.strip()
    if git("merge-base", "--is-ancestor", "origin/main", "HEAD").returncode == 0:
        return
    message = f"HEAD={head} origin/main={main_sha}; rebase onto origin/main"
    if not force or not reason or not reason.strip():
        raise SystemExit("error: stale installer: " + message)
    print("WARNING: forced stale installation: " + message + "; reason=" + reason, flush=True)


def locked_main() -> int:

    import fcntl
    import signal

    args = parse_args()
    if not (args.check_hook_paths or args.preview or args.uninstall or args.check):
        verify_install_revision(Path(__file__).resolve().parent, args.force, args.reason)
    home = args.home.expanduser().resolve()
    digest = hashlib.sha256(str(home).encode()).hexdigest()[:16]
    lock_dir = Path(tempfile.gettempdir())
    lock_path = lock_dir / f"agent-lb-install-policy-{os.getuid()}-{digest}.lock"
    with open(lock_path, "a+") as handle:
        deadline = time.monotonic() + 600
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                handle.seek(0)
                try:
                    holder = json.loads(handle.read())
                    pid = int(holder["pid"])
                    stale = time.time() - float(holder["progress_at"]) > 900
                    started = time.time() - float(holder["started_at"]) > 900
                except (ValueError, KeyError, TypeError):
                    stale = started = False
                if stale and started:
                    process = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                             capture_output=True, text=True, timeout=10).stdout.strip()
                    if "install-policy.py" in process and "python" in process and "claude" not in process.lower():
                        print(f"WARNING: retiring stalled policy installer pid={pid}; "
                              "no recorded progress for over 15 minutes", flush=True)
                        os.kill(pid, signal.SIGTERM)
                        until = time.monotonic() + 30
                        while time.monotonic() < until:
                            try:
                                os.kill(pid, 0)
                            except ProcessLookupError:
                                break
                            time.sleep(1)
                        else:
                            os.kill(pid, signal.SIGKILL)
                        continue
                if time.monotonic() > deadline:
                    raise SystemExit(f"error: another install-policy run holds {lock_path}")
                time.sleep(1)
        started_at = time.time()
        def progress():
            handle.seek(0)
            handle.truncate()
            json.dump({"pid": os.getpid(), "started_at": started_at, "progress_at": time.time()}, handle)
            handle.flush()
        progress()
        class ProgressStream:
            def __init__(self, stream):
                self.stream = stream
            def write(self, text):
                if text.strip():
                    progress()
                return self.stream.write(text)
            def flush(self):
                return self.stream.flush()
        stdout = sys.stdout
        sys.stdout = ProgressStream(stdout)
        try:
            result = main()
            if result == 0 and not (args.check_hook_paths or args.preview or args.uninstall
                                    or args.check or args.no_box_sync):
                sync_policy_boxes(home)
            return result
        finally:
            sys.stdout = stdout
            handle.seek(0)
            handle.truncate()


if __name__ == "__main__":
    raise SystemExit(locked_main())
