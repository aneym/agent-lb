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
MODEL = "claude-fable-5"
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
        return text
    start = text.index(START)
    end = text.index(END, start) + len(END)
    updated = text[:start].rstrip() + "\n\n" + text[end:].lstrip("\n")
    return updated.rstrip() + "\n" if updated.strip() else ""


def is_owned_hook(command: Any) -> bool:
    return isinstance(command, str) and "ccdex-gpt-only.sh" in command


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
    if groups:
        if cleaned:
            hooks["PreToolUse"] = cleaned
        else:
            hooks.pop("PreToolUse", None)
        if not hooks:
            updated.pop("hooks", None)
    if not uninstall:
        updated["model"] = MODEL
    return updated


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
    desired_settings_text = json.dumps(desired_settings, indent=2) + "\n"
    changes = {path: desired for path, desired in desired_docs.items() if desired != originals[path]}
    if desired_settings_text != settings_text:
        changes[settings_path] = desired_settings_text

    action = (
        "remove managed routing configuration from" if args.uninstall else "converge managed routing configuration in"
    )
    if args.preview:
        for path in changes:
            print(f"would {action} {path}")
        if not changes:
            print("managed routing configuration already converged")
        return 0
    if not changes:
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
    (checkpoint / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"checkpoint {checkpoint}")
    for path, content in changes.items():
        write_atomic(path, content)
        print(f"updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
