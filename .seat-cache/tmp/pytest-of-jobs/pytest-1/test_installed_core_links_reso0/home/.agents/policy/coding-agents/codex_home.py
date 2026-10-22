from __future__ import annotations

import json
import re
from pathlib import Path

OFFLOAD_TAG = "box-offload-hook"
OFFLOAD_COMMAND = (
    'H="$HOME/.local/bin/box-offload-hook"; P="$HOME/.local/share/uv/python/cpython-3.14-macos-aarch64-none/bin/python3.14"; '
    '[ -x "$P" ] || P=python3; if [ -r "$H" ]; then exec "$P" "$H"; fi'
)


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
    groups = data["hooks"].setdefault("PreToolUse", [])
    kept: list[dict[str, object]] = []
    for group in groups:
        hooks = [h for h in group.get("hooks", []) if OFFLOAD_TAG not in str(h.get("command", ""))]
        if hooks or not group.get("hooks"):
            kept.append(dict(group, hooks=hooks))
    if install:
        entry: dict[str, object] = {"type": "command", "command": OFFLOAD_COMMAND, "timeout": 20}
        kept.append({"matcher": "Bash", "hooks": [entry]})
    data["hooks"]["PreToolUse"] = kept
    desired = json.dumps(data, indent=2) + "\n"
    return text if text.strip() and json.loads(text) == data else desired


def codex_changes(home: Path, model: str | None) -> dict[Path, str]:
    changes = {}
    config = home_file(home, "config.toml") if model else None
    if config and model and reconcile_codex_config(config[1], model) != config[1]:
        changes[config[0]] = reconcile_codex_config(config[1], model)
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
