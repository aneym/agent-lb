from __future__ import annotations  # noqa: I001

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from claude_config_hooks import claude_config_hooks  # type: ignore[missing-import]
from floor_hooks import mentioned_floors  # type: ignore[missing-import]
from policy_documents import read_text  # type: ignore[missing-import]
from policy_hook_groups import drop_hooks, is_settings_hook, settled_matcher  # type: ignore[missing-import]  # fmt: skip
from policy_layout import EFFORT_LEVEL, HAIKU_ENV, MANAGED_AGENTS, MODEL, OBSERVATIONAL_HOOKS, POLICY_DIR, POLICY_SNAPSHOT, RELAY_SEATS, SETTINGS_HOOKS, SOL_ID, SOL_MODEL, SONNET_ENV, SONNET_ID, SONNET_MODEL  # type: ignore[missing-import]  # noqa: I001  # fmt: skip


def resolve_sol(source: Path, home: Path) -> str:

    route = source.parent.parent / "clients" / "route"
    if not route.is_file():
        route = home / ".agent-lb" / "bin" / "route"
    try:
        result = subprocess.run(
            [sys.executable, str(route), "resolve", "sol-latest"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=os.environ | {"ROUTE_TABLE": str(source / "routing-table")},
        )
    except (OSError, subprocess.SubprocessError):
        return SOL_MODEL
    model = result.stdout.strip() if result.returncode == 0 else ""
    return model if SOL_ID.fullmatch(model) else SOL_MODEL


def policy_snapshot(source: Path, home: Path) -> dict[str, Any]:
    route = source.parent.parent / "clients/route"
    if not route.is_file():
        route = home / ".agent-lb/bin/route"
    try:
        result = subprocess.run(
            [sys.executable, str(route), "policy", "snapshot", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            env=os.environ | {"ROUTE_TABLE": str(source / "routing-table")},
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
    if name in RELAY_SEATS:
        model = snapshot_model(snapshot, snapshot["standards"]["relay"]["wrapper_model"])
        effort = "low"
    elif not isinstance(seat, dict):
        return text
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
        background = snapshot_model(
            snapshot,
            snapshot["background_model"]
            if "background_model" in snapshot
            else snapshot["standards"]["background_model"],
        )
        if (
            settings.get("effortLevel") != EFFORT_LEVEL
            or settings.get("model") != MODEL
            or settings.get("env", {}).get(HAIKU_ENV) != background
            or settings.get("env", {}).get(SONNET_ENV) != snapshot_model(snapshot, "sonnet-latest")
        ):
            problems.append(".claude/settings.json")
        if json.loads(read_text(home / POLICY_SNAPSHOT)) != snapshot:
            problems.append(str(POLICY_SNAPSHOT))
    except (ValueError, KeyError, TypeError, AttributeError):
        problems.append("settings or snapshot missing/invalid")
    for problem in problems:
        print(f"policy drift: {problem}")
    return int(bool(problems))


def internal_hook_commands(hooks: dict[str, Any]) -> None:

    for groups in hooks.values():
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook.get("command")
                if not isinstance(command, str) or mentioned_floors(command):
                    continue

                command = re.sub(r'/[^\s"\':;]*/repos/skill-stats/', "$HOME/.local/share/skill-stats/", command)

                match = re.fullmatch(r'((?:"[^"\n]+"|[^\s]+)\s+)?"((?:\$HOME)?/[^"\n]+)"(.*)', command)
                if match and "/.local/share/skill-stats/" in command and "# hook missing: fail open" not in command:
                    script = match[2]
                    command = (
                        f'if [ -r "{script}" ]; then {command}; else '
                        f"printf '%s\\n' \"hook warning: missing {script}; continuing\" >&2; "
                        "fi # hook missing: fail open"
                    )
                hook["command"] = command


def reconcile_settings(
    settings: dict[str, Any],
    uninstall: bool,
    sonnet_model: str = SONNET_MODEL,
    source: Path | None = None,
    retired_kept: frozenset[str] = frozenset(),
    home: Path | None = None,
) -> dict[str, Any]:
    updated = json.loads(json.dumps(settings))
    hooks = updated.get("hooks", {})
    if isinstance(hooks, dict):
        hooks, dropped, refused = drop_hooks(hooks, retired_kept)
        for line in dropped:
            print(f"dropped {line}")
        for line in refused:
            print(f"refused retiring {line}")
        updated["hooks"] = hooks
        if home is not None and not uninstall:
            claude_config_hooks(home, hooks)
    if not uninstall:
        for event, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    command = hook.get("command", "")
                    if event == "PreToolUse" or mentioned_floors(command):
                        continue
                    if "skill-stats" in command:
                        hook["timeout"] = 3
                    if any(event == name and marker in command for name, marker in OBSERVATIONAL_HOOKS):
                        hook["async"] = True
                    settled = settled_matcher(event, group.get("matcher"), command)
                    if settled != group.get("matcher"):
                        group["matcher"] = settled
            hooks[event] = [g for g in groups if g["hooks"]]
    groups = hooks.get("PreToolUse", [])
    cleaned: list[dict[str, Any]] = []
    for group in groups:
        group_copy = dict(group)
        remaining = list(group.get("hooks", []))
        if remaining:
            group_copy["hooks"] = remaining
            cleaned.append(group_copy)
    if uninstall:
        for event in dict.fromkeys(entry[0] for entry in SETTINGS_HOOKS):
            managed = [entry[2] for entry in SETTINGS_HOOKS if entry[0] == event]
            kept = [
                {
                    **group,
                    "hooks": [
                        hook
                        for hook in group.get("hooks", [])
                        if mentioned_floors(hook.get("command"))
                        or not any(is_settings_hook(hook.get("command"), owned) for owned in managed)
                    ],
                }
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
            present = [
                existing
                for group in event_groups
                if matcher is None or group.get("matcher") in matchers
                for existing in group.get("hooks", [])
                if is_settings_hook(existing.get("command"), hook)
            ]
            if present:
                for existing in present:
                    existing.pop("async", None)
                    existing.update(hook.get("async") and {"async": True} or {})
                continue
            if matcher in ("Agent", "Workflow"):
                group = next((group for group in event_groups if group.get("matcher") == matcher), None)
                if group is not None:
                    group.setdefault("hooks", []).insert(0, dict(hook))
                    continue
            new_group: dict[str, Any] = {"hooks": [dict(hook)]}
            if matcher is not None:
                new_group["matcher"] = matcher
            event_groups.append(new_group)
    if not uninstall and source is not None:
        internal_hook_commands(updated.get("hooks", {}))
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
