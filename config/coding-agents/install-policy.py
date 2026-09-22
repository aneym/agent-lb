#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

START = "<!-- agent-lb:coding-agent-routing:start -->"
END = "<!-- agent-lb:coding-agent-routing:end -->"
MODEL = "opus"
EFFORT_LEVEL = "high"
MANAGED_AGENTS = (
    (
        Path(".claude/agents/implementer.md"),
        Path(".agent-lb/managed/coding-agents/implementer"),
        "agent-lb:implementer:v1\n",
        Path("agents/implementer.md"),
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
        Path(".claude/agents/cursor-seat.md"),
        Path(".agent-lb/managed/coding-agents/cursor-seat"),
        "agent-lb:cursor-seat:v1\n",
        Path("agents/cursor-seat.md"),
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
)
# Seats retired by the owner's 2026-09-22 lineup (no Codex Astra). The
# installer removes them; the checkpoint keeps the removed copy.
RETIRED_AGENTS = (Path(".claude/agents/astra.md"),)
# The live routing table keeps the `overrides` that `route learn` writes; the
# installer replaces everything else from the canonical table.
ROUTING_TABLE = Path(".agent-lb/managed/coding-agents/routing-table.json")
ROUTING_TABLE_OWNER = (Path(".agent-lb/managed/coding-agents/routing-table"), "agent-lb:routing-table:v1\n")
# Mirror of this directory that ROUTING.md names as the canonical path.
POLICY_DIR = Path(".agents/policy/coding-agents")
POLICY_FILES = (
    "ROUTING.md",
    "claude-adapter.md",
    "install-policy.py",
    "verify-routing",
    "routing-table.json",
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


def reconcile_settings(settings: dict[str, Any], uninstall: bool) -> dict[str, Any]:
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
            {**group, "hooks": [hook for hook in group.get("hooks", []) if not is_seat_guard_hook(hook.get("command"))]}
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
    desired_settings = reconcile_settings(settings, args.uninstall)
    desired_settings_text = json.dumps(desired_settings, indent=2, ensure_ascii=False) + "\n"
    changes: dict[Path, str | None] = {
        path: desired for path, desired in desired_docs.items() if desired != originals[path]
    }
    # Compare parsed settings so a formatting-only difference never rewrites the file.
    if desired_settings != settings:
        changes[settings_path] = desired_settings_text
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
            if agent_owned:
                changes[owner_path] = None
                if agent_text == agent_template:
                    changes[agent_path] = None
                elif agent_path.exists():
                    preserved_agents.append(("customized", agent_path))
            elif agent_path.exists():
                preserved_agents.append(("unmanaged", agent_path))
        else:
            if agent_text != agent_template:
                changes[agent_path] = agent_template
            if not agent_owned:
                changes[owner_path] = owner_marker

    if not args.uninstall:
        for relative in RETIRED_AGENTS:
            if (args.home / relative).exists():
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
        # The canonical path must hold exactly what was installed, so a symlink into
        # a checkout (whose working tree can drift) is replaced by a real copy.
        replace_policy_link = policy_dir.is_symlink()
        if replace_policy_link:
            policy_link_target = os.readlink(policy_dir)
        if replace_policy_link or policy_dir.resolve() != source:
            policy_sources = [Path(name) for name in POLICY_FILES]
            policy_sources += [path.relative_to(source) for path in sorted((source / "agents").glob("*.md"))]
            policy_sources += [path.relative_to(source) for path in sorted((source / "hooks").glob("*.py"))]
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
    for path, content in changes.items():
        if content is None:
            path.unlink()
            print(f"removed {path}")
        else:
            write_atomic(path, content)
            if path.suffix == ".py" or path.name in ("verify-routing", "route"):
                os.chmod(path, path.stat().st_mode | 0o111)
            print(f"updated {path}")
    for reason, path in preserved_agents:
        print(f"preserved {reason} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
