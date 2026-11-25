from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from codex_catalog import catalog_changes  # type: ignore[missing-import]
from codex_features import feature_changes  # type: ignore[missing-import]
from codex_tool_scope import tool_scope_changes  # type: ignore[missing-import]

OFFLOAD_COMMAND = (
    'H="$HOME/.local/bin/box-offload-hook"; P="$HOME/.local/share/uv/python/cpython-3.14-macos-aarch64-none/bin/python3.14"; '
    '[ -x "$P" ] || P=python3; if [ -r "$H" ]; then exec "$P" "$H"; fi'
)
BRANCH_GUARD_COMMAND = '/usr/bin/python3 "$HOME/.claude/hooks/branch-guard.py"'
RETIRED_COMMANDS = ('/usr/bin/python3 "$HOME/.claude/hooks/kill-scope-guard.py"',)
HOOKS_OFF_COMMAND = '/usr/bin/python3 "$HOME/.claude/hooks/hooks-off-guard.py"'
WALK_GUARD_COMMAND = '/bin/bash "$HOME/.claude/hooks/wide-scan-guard.sh" --block-walks'
OUTBOUND_COMMAND = '/usr/bin/python3 "$HOME/.claude/hooks/outbound-send-guard.py"'
MEMORY_COMMAND = '/usr/bin/python3 "$HOME/.claude/hooks/codex-memory-brief.py"'
CLAIM_WRAP_COMMAND = '/usr/bin/python3 "$HOME/.claude/hooks/claim-code-redact.py" codex'
CLAIM_OUTPUT_COMMAND = '/usr/bin/python3 "$HOME/.claude/hooks/claim_codes.py"'
STOP_BY_NAME_COMMAND = (
    'G="$HOME/.claude/hooks/stop-by-name-guard.py"; if [ -r "$G" ]; then exec /usr/bin/python3 "$G"; fi; '
    "if grep -Eq '(^|[^A-Za-z0-9_-])(pkill|killall5?)([^A-Za-z0-9_-]|$)|fuser.* -[A-Za-z]*k|(pgrep|pidof|lsof).*kill|kill.*(pgrep|pidof|lsof)'; "
    'then echo "BLOCKED: stop by name. $G is missing because claude-config has not synced to this host yet, so a command '
    "naming pkill, killall, fuser -k, or kill next to a pgrep, pidof or lsof lookup is refused until it does. Stop only "
    'the PID you recorded when you started it, with kill <pid>; free a port with port-free <port>." >&2; exit 2; fi'
)
BRIDGE_RESTART_COMMAND = (
    'G="$HOME/.claude/hooks/bridge-restart-guard.py"; if [ -r "$G" ]; then exec /usr/bin/python3 "$G" codex; fi; exit 0'
)
PY_FLOOR_HOOKS = (
    "gh-write-guard.py",
    "stash-guard",
    "rm-dynamic-deny",
    "user-manager-guard.py",
    "host-credential-guard.py",
)
SH_FLOOR_HOOKS = ("dangerous-command-guard.sh", "railway-vars-guard.sh", "link-cli-guard.sh")
BASH_FLOOR_COMMANDS = tuple(f'/usr/bin/python3 "$HOME/.claude/hooks/{name}"' for name in PY_FLOOR_HOOKS) + tuple(
    f'/bin/bash "$HOME/.claude/hooks/{name}"' for name in SH_FLOOR_HOOKS
)
BOOTOUT_GUARD_COMMAND = '/bin/bash "$HOME/.claude/hooks/agent-lb-bootout-guard.sh"'
FLOOR_COMMANDS = (
    BRANCH_GUARD_COMMAND,
    HOOKS_OFF_COMMAND,
    WALK_GUARD_COMMAND,
    OUTBOUND_COMMAND,
    *BASH_FLOOR_COMMANDS,
    BOOTOUT_GUARD_COMMAND,
    CLAIM_WRAP_COMMAND,
    CLAIM_OUTPUT_COMMAND,
)
TRUSTED_COMMANDS = (*FLOOR_COMMANDS, STOP_BY_NAME_COMMAND, BRIDGE_RESTART_COMMAND, MEMORY_COMMAND)
MANAGED_HOOKS: tuple[tuple[str, str, int, str | None], ...] = (
    (OFFLOAD_COMMAND, "PreToolUse", 20, "Bash"),
    (WALK_GUARD_COMMAND, "PreToolUse", 10, "Bash"),
    (BRANCH_GUARD_COMMAND, "PreToolUse", 10, "Bash"),
    (OUTBOUND_COMMAND, "PreToolUse", 15, "Bash"),
    (OUTBOUND_COMMAND, "PreToolUse", 15, "mcp__.*"),
    (HOOKS_OFF_COMMAND, "PreToolUse", 10, "apply_patch"),
    (HOOKS_OFF_COMMAND, "PreToolUse", 10, "Bash"),
    *((command, "PreToolUse", 10, "Bash") for command in BASH_FLOOR_COMMANDS),
    *(((BOOTOUT_GUARD_COMMAND, "PreToolUse", 5, "Bash"),) if sys.platform == "darwin" else ()),
    (STOP_BY_NAME_COMMAND, "PreToolUse", 10, "Bash"),
    (BRIDGE_RESTART_COMMAND, "PreToolUse", 10, "Bash"),
    (CLAIM_WRAP_COMMAND, "PreToolUse", 5, "Bash"),
    (CLAIM_OUTPUT_COMMAND, "PostToolUse", 5, "mcp__.*"),
    (MEMORY_COMMAND, "SessionStart", 10, "startup|resume|clear|compact"),
)
MANAGED_COMMANDS = frozenset((*TRUSTED_COMMANDS, OFFLOAD_COMMAND))


def reconcile_codex_config(text: str, model: str) -> str:
    section = ""
    lines = []
    assignment = re.compile(
        r"^(\s*(model|extract_model|consolidation_model)\s*=\s*)([\"\'])(gpt-\d+(?:\.\d+)*-(?:sol|luna))(\3)(.*)$"
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


def home_file(home: Path, name: str) -> tuple[Path, str] | None:
    path = home / ".codex" / name
    if not path.is_file():
        return None
    path = path.resolve()
    try:
        path.relative_to(home.resolve())
        return path, path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        print(f"SKIP Codex {name} rewrite: not UTF-8")
    except ValueError:
        print(f"SKIP Codex {name} rewrite: target is outside --home")
    return None


def reconcile_codex_hooks(text: str, install: bool) -> str:
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict) or not isinstance(data.setdefault("hooks", {}), dict):
        raise ValueError("hooks.json is not an object with a hooks object")
    for event in dict.fromkeys(event for _command, event, _timeout, _matcher in MANAGED_HOOKS):
        kept: list[dict[str, object]] = []
        for group in data["hooks"].get(event, []):
            hooks = [
                h
                for h in group.get("hooks", [])
                if not isinstance(h, dict) or h.get("command") not in (*MANAGED_COMMANDS, *RETIRED_COMMANDS)
            ]
            if hooks or not group.get("hooks"):
                kept.append(dict(group, hooks=hooks))
        for command, wanted, timeout, matcher in MANAGED_HOOKS:
            if install and wanted == event:
                entry: dict[str, object] = {"type": "command", "command": command, "timeout": timeout}
                kept.append({"hooks": [entry]} if matcher is None else {"matcher": matcher, "hooks": [entry]})
        if kept or event in data["hooks"]:
            data["hooks"][event] = kept
    desired = json.dumps(data, indent=2) + "\n"
    return text if text.strip() and json.loads(text) == data else desired


def codex_changes(home: Path, model: str | None) -> dict[Path, str]:
    changes = {}
    config = home_file(home, "config.toml")
    if config:
        text = reconcile_codex_config(config[1], model) if model else config[1]
        text, catalog = catalog_changes(home, text, model is not None)
        text = tool_scope_changes(text, model is not None)
        text = feature_changes(text, model is not None)
        changes.update(catalog)
        if text != config[1]:
            changes[config[0]] = text
    hooks = home_file(home, "hooks.json")
    if hooks is None and model:
        hooks = (home / ".codex" / "hooks.json", "")
    if hooks:
        try:
            desired = reconcile_codex_hooks(hooks[1], model is not None)
        except ValueError as exc:
            print(f"SKIP Codex hooks.json rewrite: {exc}")
        else:
            if desired != hooks[1]:
                changes[hooks[0]] = desired
    return changes
