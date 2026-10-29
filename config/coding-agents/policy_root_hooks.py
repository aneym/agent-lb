from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

HELPER = Path("/usr/local/sbin/agent-floors")


def root_floors() -> bool:
    return HELPER.is_file()


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
