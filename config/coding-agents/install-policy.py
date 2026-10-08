#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
MODEL = "opus"
EFFORT_LEVEL = "high"
# Claude Code 2.1.284 still maps its `sonnet` alias to claude-sonnet-5. Pin the alias to
# the newest Sonnet so every `model: sonnet` seat and sonnet-latest runs it (2026-09-28).
# The id is what `route resolve sonnet-latest` gives (the newest on the upstream list);
# SONNET_MODEL is the fallback when route cannot answer.
SONNET_ENV = "ANTHROPIC_DEFAULT_SONNET_MODEL"
SONNET_MODEL = "claude-sonnet-5-5"
SONNET_ID = re.compile(r"claude-sonnet-\d+(?:-\d{1,2})?")
SOL_MODEL = "gpt-6.1-sol"
SOL_ID = re.compile(r"gpt-\d+(?:\.\d+)*-sol")
MANAGED_AGENTS = (
    (
        Path(".agent-rails/workflows/codex-lab-home.py"),
        Path(".agent-lb/managed/coding-agents/codex-seat-home"),
        "agent-lb:codex-seat-home:v1\n",
        Path("codex-seat-home.py"),
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
        # Adopted 2026-10-08 (hook-dispatcher-3): it had no source, so every hand edit of the live copy unpinned it.
        # Edit this source only; hook-dispatch.py SCRIPT_PREFILTERS pins its sha256: re-pin on a change.
        Path(".claude/hooks/wide-scan-guard.sh"),
        Path(".agent-lb/managed/coding-agents/wide-scan-guard"),
        "agent-lb:wide-scan-guard:v1\n",
        Path("hooks/wide-scan-guard.sh"),
    ),
    (
        Path(".claude/hooks/railway-vars-guard.sh"),
        Path(".agent-lb/managed/coding-agents/railway-vars-guard"),
        "agent-lb:railway-vars-guard:v1\n",
        Path("hooks/railway-vars-guard.sh"),
    ),
    (
        # Copy the guard without registering it as a hook.
        Path(".claude/hooks/plutil-guard.sh"),
        Path(".agent-lb/managed/coding-agents/plutil-guard"),
        "agent-lb:plutil-guard:v1\n",
        Path("hooks/plutil-guard.sh"),
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
# Guards adopted from a live copy another config registers (settings.json names them, not this installer):
# uninstall leaves the file in place and drops only the ownership marker, since removing it would turn the guard's
# registration into a missing executable that never denies (2026-10-08 review M3).
ADOPTED_KEEP = (Path(".claude/hooks/wide-scan-guard.sh"), Path(".claude/hooks/railway-vars-guard.sh"))
# Retired seats: astra (owner lineup 2026-09-22, no Codex Astra) and
# implementer (2026-09-25, its terra-latest model is unserved). The installer
# removes the definition, its ownership marker and the policy mirror copy; the
# checkpoint keeps what it removed.
RETIRED_AGENTS = ("astra", "implementer")
# The live routing table keeps the `overrides` that `route learn` writes; the
# installer replaces everything else from the canonical table.
ROUTING_TABLE = Path(".agent-lb/managed/coding-agents/routing-table.json")
ROUTING_TABLE_OWNER = (Path(".agent-lb/managed/coding-agents/routing-table"), "agent-lb:routing-table:v1\n")
# Mirror of this directory that ROUTING.md names as the canonical path.
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--print", action="store_true", dest="preview")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument(
        "--hook-dispatcher", choices=("on", "off"),
        help="on: fold the PreToolUse, PostToolUse, UserPromptSubmit and Stop hooks into hooks/hook-dispatch.py "
        "after the parity fixture passes against this home's guards; off: restore the per-hook config verbatim. "
        "Absent: keep the current state (a folded config stays folded, over the guards it was checked on).",
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
    "command": (
        '/usr/bin/python3 "$HOME/.claude/hooks/seat-guard.py" 2>/dev/null || { printf %s '
        '\'{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":'
        '"seat-guard advisory: routing telemetry unavailable; dispatch was not blocked. Check agent-lb status."}}\'; }'
    ),
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


def is_closeout_hook(command: Any) -> bool:
    return isinstance(command, str) and "hooks/subagent-closeout.py" in command


def is_seat_guard_hook(command: Any) -> bool:
    return isinstance(command, str) and "hooks/seat-guard.py" in command


def is_workflow_seat_guard_hook(command: Any) -> bool:
    return isinstance(command, str) and "hooks/workflow-seat-guard.py" in command


# ---------------------------------------------------------------------------------------------------- hook dispatcher
# studio-load-fix U1 (2026-10-07): every Bash call ran 15 PreToolUse hooks and 1 PostToolUse hook, each its own
# process tree. `--hook-dispatcher on` folds the hook groups of these events into one settings entry per matcher,
# `python3 "$HOME/.claude/hooks/hook-dispatch.py" <Event> '<matcher>'`, which runs that matcher's hooks from
# hooks/dispatch/registry.json. The registry keeps the per-hook config verbatim, so `--hook-dispatcher off`
# restores it exactly. Absent the flag the state is sticky: a folded config is refolded over only the groups the
# parity fixture passed on; a group added or changed since stays a per-hook entry until the next `on`. A sticky run
# whose fixture fails twice keeps the installed fold as it is, never unfolds (a flake on a loaded machine must not
# drop the dispatcher). Each fold drops the hooks DISPATCH_DROPPED names. Rollback reads only a registry of the
# settings' own fold that accounts for every hook it folded (registry_problem); with none, nothing is written.
DISPATCH_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
DISPATCH_SCRIPT = "hooks/hook-dispatch.py"
DISPATCH_REGISTRY = Path(".claude/hooks/dispatch/registry.json")
DISPATCH_PARITY = Path(".claude/hooks/dispatch/parity-last.json")
# Python guards reviewed for in-process runs (hook-dispatch.py still refuses any whose source has a hazard). The
# seat guards (money path), load-governor and herdr-lane-autoclose (fork), alex-said and every node or shell hook
# run as their own process, as before.
DISPATCH_INPROC = (
    "rm-dynamic-deny", "desktop-guard", "stash-guard", "herdr-shell-host-guard.py", "rm-cd-rewrite.py",
    "no-direct-merge.py", "box-offload-hook", "lane-bulletin-hook", "repeat-read-warn.py", "aside-regular-tabs.py",
    "workflow-relay-guard.py", "routing-pulse.py", "herdr-tab-autoname.py", "idle-agents.py",
)
# Matchers whose hooks the parity fixture cannot run safely (AskUserQuestion files a real ask) stay per-hook.
DISPATCH_SKIP_MATCHERS = ("AskUserQuestion",)
DISPATCH_HOOK_KEYS = {"type", "command", "timeout", "statusMessage"}
SIMPLE_MATCHER = re.compile(r"[A-Za-z0-9_]+(?:\|[A-Za-z0-9_]+)*")
DEFAULT_HOOK_TIMEOUT = 600
# Claude Code 2.1.293's matcher (read from its bundle, 2026-10-07): a matcher of only these characters is a list of
# exact tool names split on `|` or `,`, each read through its alias table; anything else is a regex searched in the
# tool name and the names that alias to it.
NAME_LIST = re.compile(r"[a-zA-Z0-9_|, -]+")
TOOL_ALIASES = {"Task": "Agent", "KillShell": "TaskStop", "KillBash": "TaskStop", "ListPeers": "ListAgents",
                "Brief": "SendUserMessage", "ListMcpResources": "ListMcpResourcesTool",
                "ReadMcpResource": "ReadMcpResourceTool", "ReadMcpResourceDir": "ReadMcpResourceDirTool"}
# Dropped at the fold (simplify lead, 2026-10-07: repos format in their own checks): the global PostToolUse
# `npx prettier --write` hook on Edit|Write|MultiEdit, by the sha256 of its exact command.
DISPATCH_DROPPED = {"55c67a61ea18f3d6d58b572fe09f1699073e09ddbbc05b2d15ccf853937501d7": "global prettier --write"}


def is_dispatch_hook(hook: Any) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and DISPATCH_SCRIPT in hook["command"]


def matcher_key(group: dict[str, Any]) -> str | None:
    """'*' for a match-all group, the matcher for a plain `A|B` one, None for a regex Claude Code matches itself."""
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
    """Which groups of one event may fold: plain matcher, plain command hooks, no command shared by overlapping keys."""
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
    """Claude Code runs a command once per tool call even when two matched groups list it; across two dispatcher
    entries, or a dispatcher entry and a group left per-hook, it would run twice. So a folding group that shares a
    command with another group that may match the same tool stays per-hook, unless both fold into the same key's
    entry (deduplicated there). The other group may itself be per-hook (2026-10-08 review: `Bash:[G, true]` beside
    `^Bash$:[G]` folded the first and ran G twice). Repeats until stable: each pass only unfolds."""
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
    """Whether a group may match a tool that matcher key `key` matches (a regex is tested on each name; doubt says
    yes)."""
    if not isinstance(group, dict):
        return True
    own = matcher_key(group)
    if own is not None:
        return keys_overlap(own, key)
    if key == "*" or not isinstance(group.get("matcher"), str):
        return True
    if NAME_LIST.fullmatch(group["matcher"]):  # `Bash, Read` and the like: a name list to Claude Code
        return keys_overlap(group["matcher"], key)
    try:
        pattern = re.compile(group["matcher"])
    except re.error:
        return True
    names = tool_names(key)
    names |= {alias for alias, tool in TOOL_ALIASES.items() if tool in names}
    return any(pattern.search(name) for name in names)


def keep_config_order(groups: list[Any], eligible: list[bool]) -> list[bool]:
    """A fold runs all of a key's groups at the place of its first group. That keeps config order (which decides the
    last updatedInput and the order of messages) only when no group that may match the same tool sits between them;
    a group past such a group stays per-hook, in place. Repeats until stable: each pass only unfolds."""
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
    """The hook config without the hooks DISPATCH_DROPPED names (a group left empty goes too), and what went."""
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
    """Why a registry cannot be trusted to restore the per-hook config, or None. Its per_hook block must account for
    every hook it folded: each entries hook sits in a per_hook group under the same matcher key, each per_hook group
    is either still in the folded list verbatim or folded whole into its key's entry, and each dispatcher entry of the
    folded list names a key of entries."""
    if not isinstance(registry, dict):
        return "not a registry"
    per_hook, entries, dispatch = registry.get("per_hook"), registry.get("entries"), registry.get("dispatch")
    if not (isinstance(per_hook, dict) and isinstance(entries, dict) and isinstance(dispatch, dict)):
        return "per_hook, entries or dispatch is missing"
    # The rev names the hooks the fold runs; entries edited since no longer hash to it (2026-10-08 review M2).
    if registry.get("rev") and registry_rev(entries) != registry["rev"]:
        return f"entries do not hash to rev {registry['rev']}"
    # Every event any block names is checked, not only the events of entries (2026-10-08 review M2: `entries: {}`
    # beside an intact dispatch list and an empty per_hook passed).
    for event in dict.fromkeys(list(entries) + list(per_hook) + list(dispatch)):
        keyed = entries.get(event)
        groups = per_hook.get(event)
        folded = dispatch.get(event)
        if not isinstance(keyed, dict) or not isinstance(groups, list) or not isinstance(folded, list):
            return f"{event}: entries, per_hook or dispatch is not the right shape"
        # Each entry is exactly what the fold derives from the per_hook groups folded into it: the same hook records
        # (timeout and every other field, not only the command), deduplicated by command, in config order (2026-10-08
        # review M3: a per_hook timeout of 0.001 beside an entry timeout of 1 passed).
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
    """Names a fold by the hooks it runs, so an unchanged fold keeps its settings bytes."""
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
    """Fold per-hook groups into dispatcher entries. `only` limits folding to these groups (sticky refold);
    `worth(hooks)` says whether one matcher's hooks gain from the dispatcher (a lone external hook does not).

    Returns (folded hooks, registry) or (hooks, None) when nothing folds."""
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
            # A group left per-hook by `worth` can now sit between the groups of another key.
            eligible = keep_config_order(groups, eligible)
        while True:  # a group the steps above left per-hook can share a command with one still folding
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
    """The per-hook config behind a folded one: verbatim when nothing else changed it since the fold."""
    unfolded = json.loads(json.dumps(hooks))
    for event in DISPATCH_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not any(
            isinstance(group, dict) and any(is_dispatch_hook(hook) for hook in group.get("hooks") or [])
            for group in groups
        ):
            continue
        # Another installer may append its hooks to a dispatcher group (the open-factory tool did, 2026-10-08). Claude
        # Code runs a group's hooks in parallel, so that group is the dispatcher group plus a group of the added hooks
        # under the same matcher; the added ones stay per-hook, after the restored config.
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
        # Something edited the folded list since the fold. Keep the original order: each folded group returns at its
        # old place while its dispatcher entry is still there; a group left per-hook stays while it is still there;
        # groups added since follow, in their order.
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
REV_KEEP_SECONDS = 30 * 86400  # a session keeps the hooks it started with; its registry stays this long


def folded_rev(settings: dict[str, Any]) -> str | None:
    """The registry rev the folded settings entries run, or None (no rev: an entry from before revs)."""
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    for event in DISPATCH_EVENTS:
        for group in hooks.get(event) or []:
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
                match = DISPATCH_REV.search(hook["command"]) if is_dispatch_hook(hook) else None
                if match:
                    return match.group(1)
    return None


def read_registry(home: Path, rev: str | None = None) -> dict[str, Any] | None:
    """The registry of the fold these settings run (same rev, or none for entries without one) that can restore the
    per-hook config: the first consistent copy (registry_problem) of rev file, registry.json, its backup, and for
    entries without a rev registry.legacy.json. A damaged copy is passed over for a good one; None when no copy is
    good, so the caller leaves the settings as they are."""
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
    """A matcher gains from the dispatcher when it has two or more hooks, or one the dispatcher runs without a
    process of its own (in-process, prefiltered or exec'd); a lone hook that would spawn anyway stays per-hook."""
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
        # Plan as a hook would run: this home, a UTF-8 locale (Claude Code sessions have one).
        os.environ.pop("LC_ALL", None)
        os.environ.pop("LC_CTYPE", None)
        os.environ.update(HOME=str(home), LANG="en_US.UTF-8")
        try:
            plan = module.plan_hook(hooks[0], dict.fromkeys(DISPATCH_INPROC))
        except Exception:
            return False
        finally:
            os.environ.clear()
            os.environ.update(saved)
        return plan.kind != "external"

    return worth


SCRIPT_WORD = re.compile(r'\s*(?:(?:/usr/bin/)?python3\s+)?("[^"]+"|[^\s"\'|;&<>]+)')


def pin_inproc(registry: dict[str, Any], home: Path, source: Path) -> dict[str, Any]:
    """Pin the dispatcher and each in-process guard to the bytes the parity fixture checks."""
    pins: dict[str, str] = {}
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
                if path.name in DISPATCH_INPROC and path.is_file():
                    pins[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    pinned = dict(registry)
    pinned["inproc_sha"] = dict(sorted(pins.items()))
    pinned["dispatcher_sha256"] = hashlib.sha256((source / DISPATCH_SCRIPT).read_bytes()).hexdigest()
    return pinned


def run_parity(source: Path, home: Path, registry: dict[str, Any]) -> tuple[bool, str]:
    """Run the parity fixture on this registry against home's own guards; the report lands beside the registry.

    Parity is the gate. The process count is measured and reported, not required: a group left per-hook (added since
    the last `on`) costs its own process and must not undo the fold of the rest."""
    # The dispatcher's built-in subagent rewrite intentionally differs from the old
    # per-hook path, so exercise its protocol separately before legacy parity.
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
                 # This run holds the per-home install lock; the process budget is the harden check's to prove.
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
    """The newest Sonnet id per `route resolve sonnet-latest` on this source table, else SONNET_MODEL."""
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
    """Resolve the Codex default through the same floating alias as GPT seats."""
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


def reconcile_codex_config(text: str, model: str) -> str:
    """Change only GPT Sol/Luna model values, retaining every other byte."""
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


def reconcile_settings(settings: dict[str, Any], uninstall: bool, sonnet_model: str = SONNET_MODEL) -> dict[str, Any]:
    updated = json.loads(json.dumps(settings))
    hooks = updated.get("hooks", {})
    groups = hooks.get("PreToolUse", [])
    cleaned: list[dict[str, Any]] = []
    for group in groups:
        group_copy = dict(group)
        remaining = [hook for hook in group.get("hooks", []) if not is_owned_hook(hook.get("command"))]
        if remaining:
            group_copy["hooks"] = remaining
            cleaned.append(group_copy)
    if uninstall:
        stop_groups = hooks.get("SubagentStop", [])
        kept_stop = [
            {**group, "hooks": [hook for hook in group.get("hooks", []) if not is_closeout_hook(hook.get("command"))]}
            for group in stop_groups
        ]
        kept_stop = [group for group in kept_stop if group["hooks"]]
        if kept_stop:
            hooks["SubagentStop"] = kept_stop
        else:
            hooks.pop("SubagentStop", None)
        # The managed guard file goes away on uninstall; so does its registration.
        cleaned = [
            {**group, "hooks": [hook for hook in group.get("hooks", [])
                               if not is_seat_guard_hook(hook.get("command"))
                               and not is_workflow_seat_guard_hook(hook.get("command"))]}
            for group in cleaned
        ]
        cleaned = [group for group in cleaned if group["hooks"]]
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
        # The seat guard only enforces the lineup if Claude Code runs it on every
        # Agent dispatch; register it unless some Agent hook already runs it.
        pre_tool_use = updated.setdefault("hooks", {}).setdefault("PreToolUse", [])
        registered = any(
            is_seat_guard_hook(hook.get("command"))
            for group in pre_tool_use
            if group.get("matcher") == "Agent"
            for hook in group.get("hooks", [])
        )
        if not registered:
            agent_group = next((group for group in pre_tool_use if group.get("matcher") == "Agent"), None)
            if agent_group is None:
                pre_tool_use.append({"matcher": "Agent", "hooks": [dict(SEAT_GUARD_HOOK)]})
            else:
                agent_group.setdefault("hooks", []).insert(0, dict(SEAT_GUARD_HOOK))
        if not any(
            is_workflow_seat_guard_hook(hook.get("command"))
            for group in pre_tool_use if group.get("matcher") == "Workflow"
            for hook in group.get("hooks", [])
        ):
            workflow_group = next((group for group in pre_tool_use if group.get("matcher") == "Workflow"), None)
            if workflow_group is None:
                pre_tool_use.append({"matcher": "Workflow", "hooks": [dict(WORKFLOW_SEAT_GUARD_HOOK)]})
            else:
                workflow_group.setdefault("hooks", []).insert(0, dict(WORKFLOW_SEAT_GUARD_HOOK))
        if not any(
            "hooks/railway-vars-guard.sh" in hook.get("command", "")
            for group in pre_tool_use if group.get("matcher") in ("Bash", "*")
            for hook in group.get("hooks", [])
        ):
            pre_tool_use.append({"matcher": "Bash", "hooks": [{
                "type": "command", "command": 'bash "$HOME/.claude/hooks/railway-vars-guard.sh"',
                "timeout": 10,
            }]})
        # The closeout hook writes the outcome/tokens side of the dispatch ledger.
        subagent_stop = updated["hooks"].setdefault("SubagentStop", [])
        if not any(is_closeout_hook(hook.get("command")) for group in subagent_stop for hook in group.get("hooks", [])):
            subagent_stop.append({"hooks": [dict(CLOSEOUT_HOOK)]})
    return updated


def desired_routing_table(source_path: Path, live_path: Path) -> str:
    table = json.loads(source_path.read_text())
    live_text = read_text(live_path)
    if live_text:
        try:
            live = json.loads(live_text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"error: invalid JSON in {live_path}: {exc}") from exc
        if isinstance(live, dict) and "overrides" in live:
            table["overrides"] = live["overrides"]
    return json.dumps(table, indent=2) + "\n"


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    args = parse_args()
    source = Path(__file__).resolve().parent
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
    # Every policy change below applies to the per-hook view; the dispatcher fold, if any, is redone after it.
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
            # The codex-host adapter is retired: converge always removes its managed block.
            codex_path: uninstall_adapter(originals[codex_path], codex_path),
        }
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    sonnet_model = SONNET_MODEL if args.uninstall else resolve_sonnet(source, args.home)
    desired_settings = reconcile_settings(settings, args.uninstall, sonnet_model)
    mode = "off" if args.uninstall else (args.hook_dispatcher or ("sticky" if folded_now else "off"))
    new_registry: dict[str, Any] | None = None
    keep_fold = False  # a sticky refold whose fixture failed twice keeps the fold already installed
    if mode != "off" and isinstance(desired_settings.get("hooks"), dict):
        kept_hooks, dropped = drop_hooks(desired_settings["hooks"])
        for name in dropped:
            print(f"hook dispatcher: dropped {name} at this fold")
        desired_settings = {**desired_settings, "hooks": kept_hooks}
        only = None
        if mode == "sticky" and old_registry is not None:
            # Refold only what the fixture passed on; added or edited groups stay per-hook until the next `on`.
            only = {
                event: [group for group in groups if group not in old_registry.get("dispatch", {}).get(event, [])]
                for event, groups in old_registry["per_hook"].items()
            }
        folded_hooks, candidate = fold_hooks(desired_settings["hooks"], only, dispatch_worth(source, args.home))
        if candidate is not None:
            candidate = pin_inproc(candidate, args.home, source)
            if mode == "sticky" and old_registry is not None:
                candidate["parity"] = old_registry.get("parity")
            unchanged = mode == "sticky" and old_registry is not None and all(
                candidate.get(field) == old_registry.get(field) for field in ("inproc_sha", "dispatcher_sha256")
            )
            if args.preview:
                print(f"would fold hooks into the dispatcher ({'refold' if unchanged else 'after the parity fixture'})")
                new_registry = candidate
            elif unchanged:
                new_registry = candidate
            else:
                ok, summary = run_parity(source, args.home, candidate)
                if not ok and mode == "sticky":
                    # The coding-agents sync runs this on a loaded machine: one more run before calling it a change.
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
                    # Never unfold on a failed sticky run: the installed fold passed its own fixture, and its
                    # settings entries and registry stay exactly as they are (a flake must not drop the dispatcher;
                    # a real change is reported here and by the harden check, and `on` or `off` decides it).
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
    if args.uninstall:
        if models_text.startswith(MODELS_MARKER):
            changes[models_path] = None
    else:
        routing_bytes = (source / "ROUTING.md").read_bytes()
        workspace_path = source / "WORKSPACE.md"
        workspace_bytes = workspace_path.read_bytes() if workspace_path.is_file() else b""
        rules_rev = hashlib.sha256(routing_bytes + workspace_bytes).hexdigest()[:12]
        header = (
            f'{MODELS_MARKER} from config/coding-agents/ROUTING.md (rules_rev {rules_rev}). '
            'Do not edit: the next install overwrites local edits. Change the rule in agent-lb, '
            'or ask `factory ask "<question>" --json`. History: ~/.claude/ledgers/models-ledger.md. -->'
        )
        desired_models = header + "\n\n" + routing_bytes.decode("utf-8")
        if models_path.exists() and not models_text.startswith(MODELS_MARKER):
            date = datetime.now(timezone.utc).strftime("%Y%m%d")
            backup = args.home / f".claude/ledgers/models-md-handkept-{date}.md"
            if not backup.exists():
                changes[backup] = models_text
        if desired_models != models_text:
            changes[models_path] = desired_models
    # Compare parsed settings so a formatting-only difference never rewrites the file.
    if desired_settings != disk_settings:
        changes[settings_path] = desired_settings_text
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
        # Settings entries without a rev (written before revs) read registry.json, which this run replaces or removes;
        # running sessions keep the hooks they started with, so the fold they were written with stays readable for
        # them as registry.legacy.json (the dispatcher's last candidate for an entry without a rev), for 7 days.
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
                # The kept fold runs the dispatcher its fixture passed on: the rejected one is not published over it
                # (2026-10-08 review M2), and the next run that passes publishes it.
                if agent_text != agent_template:
                    print(f"hook dispatcher: keeping the installed {relative_path.name}; the new one failed the fixture")
                continue
            if agent_text != agent_template:
                changes[agent_path] = agent_template
            if not agent_owned:
                changes[owner_path] = owner_marker

    if not args.uninstall:
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
        # The router CLI lives in the repo's clients/, so only a repo run installs it.
        route_source = source.parent.parent / "clients" / "route"
        if route_source.is_file():
            route_path = args.home / ".agent-lb" / "bin" / "route"
            if read_text(route_path) != route_source.read_text():
                changes[route_path] = route_source.read_text()
            route_owner = args.home / ".agent-lb" / "managed" / "coding-agents" / "route-cli"
            if read_text(route_owner) != "agent-lb:route-cli:v1\n":
                changes[route_owner] = "agent-lb:route-cli:v1\n"
        # `seat` runs the CLI-only seats (Cursor, Devin) on registered accounts.
        seat_source = source.parent.parent / "clients" / "seat"
        if seat_source.is_file():
            seat_path = args.home / ".agent-lb" / "bin" / "seat"
            if read_text(seat_path) != seat_source.read_text():
                changes[seat_path] = seat_source.read_text()
        # agent-lb's routing-policy view reads the Open Factory decider from here,
        # because the service runtime has no clients/ checkout.
        decider_source = source.parent.parent / "clients" / "open-factory" / "open_factory" / "decider.json"
        if decider_source.is_file():
            decider_path = args.home / ROUTING_TABLE.with_name("decider.json")
            if read_text(decider_path) != decider_source.read_text():
                changes[decider_path] = decider_source.read_text()
        # The canonical path must hold exactly what was installed, so a symlink into
        # a checkout (whose working tree can drift) is replaced by a real copy.
        replace_policy_link = policy_dir.is_symlink()
        if replace_policy_link:
            policy_link_target = os.readlink(policy_dir)
        if replace_policy_link or policy_dir.resolve() != source:
            policy_sources = [Path(name) for name in POLICY_FILES]
            policy_sources += [path.relative_to(source) for path in sorted((source / "agents").glob("*.md"))]
            # Shell guard sources too (wide-scan-guard.sh, 2026-10-08): the mirror's own install-policy.py reads them.
            policy_sources += [path.relative_to(source) for pattern in ("*.py", "*.sh")
                               for path in sorted((source / "hooks").glob(pattern))]
            for relative in policy_sources:
                wanted = (source / relative).read_text()
                # Behind a symlink every file must be written: the link is replaced
                # by an empty directory before the copies land.
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

    # The parity fixture can take a minute; settings edited meanwhile (another installer, a hand edit) would be
    # overwritten with a stale view. Refuse instead; the next run starts from the new file.
    if read_text(settings_path) != settings_text:
        raise SystemExit(f"error: {settings_path} changed while this install ran; nothing written, run it again")
    checkpoint_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    checkpoint = args.home / ".agent-lb" / "config-checkpoints" / "coding-agents" / checkpoint_name
    checkpoint.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, str] = {}
    for path in changes:
        if path.exists():
            relative = path.relative_to(args.home)
            destination = checkpoint / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            manifest[str(relative)] = "copied"
        else:
            manifest[str(path.relative_to(args.home))] = "absent"
    if replace_policy_link:
        manifest[str(POLICY_DIR)] = f"symlink -> {policy_link_target}"
        # Keep everything the link exposed, managed or not, before it is replaced.
        shutil.copytree(
            args.home / POLICY_DIR,
            checkpoint / "policy-link-target",
            ignore=shutil.ignore_patterns("__pycache__"),
            symlinks=True,
        )
    (checkpoint / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if replace_policy_link:
        policy_dir = args.home / POLICY_DIR
        policy_dir.unlink()
        # Start from everything the link exposed, so unmanaged files stay at their
        # path; the managed files written below then replace their old copies.
        shutil.copytree(checkpoint / "policy-link-target", policy_dir, symlinks=True)
        print(f"replaced symlink {policy_dir} -> {policy_link_target} with an installed copy")
    print(f"checkpoint {checkpoint}")
    # Order: the dispatcher and this fold's registry.<rev>.json land first (no settings entry names that rev yet);
    # settings then switch every entry to the new rev in one atomic write; registry.json and its backup (read only
    # by entries without a rev) follow; removals come last. At every step each settings entry runs the guards it
    # was written with, so a crash between steps leaves the old or the new config, never a mix.
    first_files = {args.home / ".claude" / DISPATCH_SCRIPT, legacy_path} | ({rev_path} if rev_path else set())
    latest_files = {registry_path, backup_path}

    def write_order(item: tuple[Path, str | None]) -> int:
        path, content = item
        if content is None:
            return 3
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


def locked_main() -> int:
    """One install at a time per home: the coding-agents sync and a hand run must not interleave their writes."""
    import fcntl
    import time

    home = parse_args().home.expanduser().resolve()
    # Outside the home, so a --print run writes nothing there.
    digest = hashlib.sha256(str(home).encode()).hexdigest()[:16]
    # A fixed directory, not TMPDIR: launchd jobs and shells see different TMPDIRs.
    lock_dir = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    lock_path = lock_dir / f"agent-lb-install-policy-{os.getuid()}-{digest}.lock"
    with open(lock_path, "a") as handle:
        deadline = time.monotonic() + 600
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise SystemExit(f"error: another install-policy run holds {lock_path}")
                time.sleep(1)
        return main()


if __name__ == "__main__":
    raise SystemExit(locked_main())
