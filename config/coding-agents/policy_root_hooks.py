from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

HELPER = Path("/usr/local/sbin/agent-floors")
CLAUDE_MANAGED = Path(
    "/Library/Application Support/ClaudeCode/managed-settings.json"
    if sys.platform == "darwin"
    else "/etc/claude-code/managed-settings.json"
)
CODEX_MANAGED = Path("/etc/codex/requirements.toml")
LAUNCH = ["-I", "-S", "/usr/local/lib/agent-floors/current/floor-root/run", "floor"]
TOML_COMMAND = re.compile(r'^command = ("(?:[^"\\]|\\.)*")$', re.M)


def root_floors() -> bool:
    return HELPER.is_file()


def root_text(path: Path) -> str:
    try:
        info = path.stat()
        return path.read_text() if info.st_uid == 0 and not info.st_mode & 0o022 else ""
    except OSError:
        return ""


def launched(argv: list[str]) -> str | None:
    return argv[5] if len(argv) > 5 and argv[1:5] == LAUNCH else None


def root_floor_names(codex: bool) -> set[str]:
    text = root_text(CODEX_MANAGED if codex else CLAUDE_MANAGED)
    argvs: list[list[str]] = []
    try:
        if codex:
            argvs = [shlex.split(json.loads(value)) for value in TOML_COMMAND.findall(text)]
        elif text:
            hooks = json.loads(text).get("hooks") or {}
            for groups in hooks.values():
                for group in groups:
                    for hook in group.get("hooks") or []:
                        if isinstance(hook.get("args"), list):
                            argvs.append([hook.get("command"), *hook["args"]])
    except (ValueError, AttributeError, TypeError):
        return set()
    return {name for name in map(launched, argvs) if name}


def held_by_root(command: str, names: set[str]) -> bool:
    return any(re.search(r"(?:^|[/\s\"'])%s(?=$|[\s\"'])" % re.escape(name), command) for name in names)


def floor_word(source: Path) -> re.Pattern[str]:
    entries = json.loads((source / "hooks" / "floors.json").read_text())["floors"]
    names: list[str] = sorted({str(entry["file"]) for entry in entries}, key=len, reverse=True)
    return re.compile(r"(?:^|[/\s\"'])(%s)(?=$|[\s\"';|&)])" % "|".join(re.escape(name) for name in names))


def without_floors(hooks: Any, word: re.Pattern[str]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for event, groups in (hooks or {}).items():
        out = []
        for group in groups if isinstance(groups, list) else []:
            items = [
                hook
                for hook in (group.get("hooks") or [])
                if not (
                    isinstance(hook, dict) and isinstance(hook.get("command"), str) and word.search(hook["command"])
                )
            ]
            if items:
                out.append({**group, "hooks": items})
        if out:
            kept[event] = out
    return kept


def desired(path: Path, changes: dict[Path, str | None]) -> dict[str, Any]:
    text = changes[path] if path in changes else (path.read_text() if path.is_file() else "")
    data = json.loads(text) if text and text.strip() else {}
    return data if isinstance(data, dict) else {}


def hand_off(source: Path, home: Path, changes: dict[Path, str | None]) -> str:
    settings_path, codex_path = home / ".claude" / "settings.json", home / ".codex" / "hooks.json"
    settings, codex = desired(settings_path, changes), desired(codex_path, changes)
    payload = {"claude": settings.get("hooks") or {}, "codex": codex.get("hooks") or {}}
    done = subprocess.run(
        ["/usr/bin/sudo", "-n", str(HELPER), "sync"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if done.returncode:
        return f"WARNING kept floor hooks in user settings; root sync failed: {done.stderr.strip()[-600:]}"
    word = floor_word(source)
    for path, data in ((settings_path, settings), (codex_path, codex)):
        if not data and not path.is_file():
            continue
        stripped = {**data, "hooks": without_floors(data.get("hooks"), word)}
        text = json.dumps(stripped, indent=2, ensure_ascii=False) + "\n"
        current = path.read_text() if path.is_file() else ""
        if text != current:
            changes[path] = text
        else:
            changes.pop(path, None)
    return "root " + done.stdout.strip()
