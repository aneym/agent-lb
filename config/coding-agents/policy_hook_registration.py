from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

REGISTRATIONS_PATH = Path(".agent-lb/hook-registrations.json")


def hook_identity(hook: dict[str, Any]) -> str:
    command = hook["command"]
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    if words and words[0].startswith(("/", "~/", "$HOME/")):
        return words[0]
    return command


def validate_registration(value: Any, origin: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(f"error: hook registration from {origin} is not an object")
    event = value.get("event")
    matcher = value.get("matcher")
    hook = value.get("hook")
    if not isinstance(event, str) or not event.isidentifier():
        raise SystemExit(f"error: hook registration from {origin} has an invalid event")
    if matcher is not None and not isinstance(matcher, str):
        raise SystemExit(f"error: hook registration for {event} has an invalid matcher")
    if not isinstance(hook, dict) or set(hook) - {"type", "command", "timeout", "statusMessage"}:
        raise SystemExit(f"error: hook registration for {event} has unsupported hook fields")
    if hook.get("type") != "command" or not isinstance(hook.get("command"), str) or not hook["command"].strip():
        raise SystemExit(f"error: hook registration for {event} is not a command hook")
    timeout = hook.get("timeout")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0
    ):
        raise SystemExit(f"error: hook registration for {event} has an invalid timeout")
    if "statusMessage" in hook and not isinstance(hook["statusMessage"], str):
        raise SystemExit(f"error: hook registration for {event} has an invalid statusMessage")
    result = {"event": event, "hook": dict(hook)}
    if matcher is not None:
        result["matcher"] = matcher
    return result


def registration_key(registration: dict[str, Any]) -> tuple[str, str, str]:
    return (
        registration["event"],
        registration.get("matcher") or "*",
        hook_identity(registration["hook"]),
    )


def load_registrations(home: Path, additions: list[str]) -> tuple[Path, list[dict[str, Any]], str]:
    path = home / REGISTRATIONS_PATH
    stored: Any = {"hooks": []}
    if path.is_file():
        try:
            stored = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise SystemExit(f"error: invalid hook registrations in {path}: {exc}") from exc
    if not isinstance(stored, dict) or not isinstance(stored.get("hooks"), list):
        raise SystemExit(f"error: invalid hook registrations in {path}: hooks is not a list")
    merged = {
        registration_key(validated): validated
        for validated in (
            validate_registration(value, str(path)) for value in stored["hooks"]
        )
    }
    for text in additions:
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise SystemExit(f"error: invalid --register-hook JSON: {exc}") from exc
        registration = validate_registration(value, "--register-hook")
        merged[registration_key(registration)] = registration
    registrations = list(merged.values())
    text = json.dumps({"schema": 1, "hooks": registrations}, indent=2, ensure_ascii=False) + "\n"
    return path, registrations, text


def registration_commands(registrations: list[dict[str, Any]]) -> set[str]:
    return {registration["hook"]["command"] for registration in registrations}


def apply_registrations(settings: dict[str, Any], registrations: list[dict[str, Any]]) -> None:
    if not registrations:
        return
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit("error: settings.json hooks is not an object")
    for registration in registrations:
        event = registration["event"]
        matcher = registration.get("matcher")
        hook = registration["hook"]
        identity = hook_identity(hook)
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise SystemExit(f"error: settings.json {event} hooks is not a list")
        found = False
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            group_matcher = group.get("matcher")
            if group_matcher != matcher and not (
                matcher in (None, "", "*") and group_matcher in (None, "", "*")
            ):
                continue
            for existing in group["hooks"]:
                if not isinstance(existing, dict) or not isinstance(existing.get("command"), str):
                    continue
                try:
                    if hook_identity(existing) != identity:
                        continue
                except (KeyError, TypeError):
                    continue
                existing.clear()
                existing.update(hook)
                found = True
        if not found:
            group = {"hooks": [dict(hook)]}
            if matcher is not None:
                group["matcher"] = matcher
            groups.append(group)
