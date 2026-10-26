#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_home import codex_changes  # type: ignore[missing-import]
from dispatch_parity import run_parity  # type: ignore[missing-import]
from harness_mcp import ensure_harness_mcp  # type: ignore[missing-import]
from hook_inventory import hook_path_problems, validate_hook_changes  # type: ignore[missing-import]
from policy_documents import install_adapter, install_codex_doc, read_text, uninstall_adapter  # type: ignore[missing-import]  # fmt: skip
from policy_files import autocompact_text, checkpoint_changes, write_atomic  # type: ignore[missing-import]
from policy_hook_groups import dispatcher_module, opaque_floor_commands  # type: ignore[missing-import]
from policy_hook_handlers import apply_handlers, handler_folds_known, load_handlers  # type: ignore[missing-import]  # fmt: skip
from policy_hook_install import dispatch_worth, managed_hooks, pin_inproc  # type: ignore[missing-import]
from policy_hook_registry import fold_hooks, folded_rev, is_folded, read_registry, registry_rev, unfold_hooks  # type: ignore[missing-import]  # fmt: skip
from policy_layout import CORE_SOURCE, DISPATCH_EVENTS, DISPATCH_REGISTRY, DISPATCH_SCRIPT, HAIKU_ENV, MODELS_FULL, MODELS_MARKER, POLICY_DIR, RETIRED_HOOKS, REV_FILE, REV_KEEP_SECONDS, SONNET_MODEL  # type: ignore[missing-import]  # noqa: I001  # fmt: skip
from policy_lock import locked_main, parse_args  # type: ignore[missing-import]
from policy_registry_retirement import kept_retired_hooks, kept_warning, retire_hook_registries  # type: ignore[missing-import]
from policy_plan import plan_managed_files  # type: ignore[missing-import]
from policy_stage import install_staged  # type: ignore[missing-import]
from policy_settings import check_policy, internal_hook_commands, policy_snapshot, reconcile_settings, resolve_sol, snapshot_model  # type: ignore[missing-import]  # noqa: I001  # fmt: skip


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


def main() -> int:
    args = parse_args()
    if args.check_hook_paths:
        problems = hook_path_problems(args.home, is_folded, read_registry, folded_rev, unfold_hooks)
        for line in problems:
            print(line)
        print("hook paths: ok" if not problems else f"hook paths: {len(problems)} problem(s)")
        return 1 if problems else 0
    source = Path(__file__).resolve().parent
    snapshot = None if args.uninstall else policy_snapshot(source, args.home)
    if args.check:
        return check_policy(source, args.home, snapshot)
    kept = kept_retired_hooks(args.home)
    for path in kept:
        print(kept_warning(path))
    claude_path = args.home / ".claude" / "CLAUDE.md"
    codex_path = args.home / ".codex" / "AGENTS.md"
    template = (source / "claude-adapter.md").read_text()
    originals = {path: read_text(path) for path in (codex_path,) if path != claude_path}
    if not args.preserve_claude_md:
        originals[claude_path] = read_text(claude_path)
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
                f"error: {exc}. Restore {registry_path} from ~/.agent-lb/config-checkpoints/coding-agents/"
            ) from exc

    try:
        desired_docs = {
            codex_path: (
                uninstall_adapter(originals[codex_path], codex_path)
                if args.uninstall
                else install_codex_doc(originals[codex_path], (source / "codex-AGENTS.md").read_text(), codex_path)
            ),
        }
        if not args.preserve_claude_md:
            desired_docs[claude_path] = (
                uninstall_adapter(originals[claude_path], claude_path)
                if args.uninstall
                else install_adapter(originals[claude_path], template, claude_path)
            )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    sonnet_model = SONNET_MODEL if args.uninstall else snapshot_model(snapshot, "sonnet-latest")
    nxt = reconcile_settings(
        settings, args.uninstall, rewrite_floor_commands, sonnet_model, source, frozenset(kept.values())
    )
    handlers = {} if args.uninstall else apply_handlers(nxt, load_handlers(args.home, source), args.home)
    if not args.uninstall:
        nxt["autoCompactWindow"] = snapshot["autocompact_window"]["tab"]
    if args.uninstall:
        if isinstance(nxt.get("env"), dict):
            nxt["env"].pop(HAIKU_ENV, None)
            if not nxt["env"]:
                nxt.pop("env")
    else:
        background = snapshot.get("background_model") or snapshot["standards"]["background_model"]
        nxt["env"][HAIKU_ENV] = snapshot_model(snapshot, background)
    mode = "off" if args.uninstall else (args.hook_dispatcher or ("sticky" if folded_now else "off"))
    if mode != "off" and isinstance(nxt.get("hooks"), dict):
        opaque = opaque_floor_commands(nxt["hooks"], source)
        if opaque:
            raise SystemExit(
                "error: nothing written: floor guards require direct exec (`[python3|bash] <script> [args]`, S44): "
                + "; ".join(opaque)
            )
    new_registry: dict[str, Any] | None = None
    keep_fold = False
    if mode != "off" and isinstance(nxt.get("hooks"), dict):
        only = None
        if mode == "sticky" and old_registry is not None:
            only = {}
            for event, groups in old_registry["per_hook"].items():
                folded_groups = json.loads(
                    json.dumps(
                        [group for group in groups if group not in old_registry.get("dispatch", {}).get(event, [])]
                    )
                )
                rewrite_floor_commands({"PreToolUse": folded_groups} if event == "PreToolUse" else {}, source)
                only[event] = folded_groups
            for event, groups in handlers.items():
                only.setdefault(event, []).extend(groups)
        folded_hooks, candidate = fold_hooks(nxt["hooks"], only, dispatch_worth(source, args.home))
        if candidate is not None:
            candidate = pin_inproc(candidate, args.home, source)
            if mode == "sticky" and old_registry is not None:
                candidate["parity"] = old_registry.get("parity")
            unchanged = (
                mode == "sticky"
                and old_registry is not None
                and all(
                    candidate.get(field) == old_registry.get(field)
                    for field in ("inproc_sha", "dispatcher_sha256", "script_sha")
                )
                and handler_folds_known(candidate, old_registry, handlers)
            )
            if args.preview:
                print(f"would fold hooks into the dispatcher ({'refold' if unchanged else 'after the parity fixture'})")
                new_registry = candidate
            elif unchanged:
                new_registry = candidate
            else:
                ok, summary = run_parity(source, args.home, candidate, managed_hooks(args.home, source))
                if not ok and mode == "sticky":
                    print(f"hook dispatcher: parity fixture failed ({summary}); running it once more")
                    ok, summary = run_parity(source, args.home, candidate, managed_hooks(args.home, source))
                if ok:
                    candidate["parity"] = {
                        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "summary": summary,
                    }
                    new_registry = candidate
                    print(f"hook dispatcher parity passed: {summary}")
                elif mode == "on":
                    raise SystemExit(f"error: hook dispatcher not installed; parity fixture failed: {summary}")
                elif old_registry and not any(n in json.dumps(old_registry.get("per_hook")) for n in RETIRED_HOOKS):
                    keep_fold = True
                    print(f"WARNING hook dispatcher: parity failed twice ({summary}); keeping installed fold")
                else:
                    print(f"WARNING hook dispatcher: parity failed ({summary}); restoring per-hook config")
            if new_registry is not None:
                nxt = {**nxt, "hooks": folded_hooks}
            elif keep_fold:
                hooks_now = dict(nxt["hooks"])
                for event in DISPATCH_EVENTS:
                    if event in (disk_settings.get("hooks") or {}):
                        hooks_now[event] = disk_settings["hooks"][event]
                    else:
                        hooks_now.pop(event, None)
                nxt = {**nxt, "hooks": hooks_now}
    nxt_text = json.dumps(nxt, indent=2, ensure_ascii=False) + "\n"
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
        section_paths = sorted((source / "routing").glob("*.md"))
        if not section_paths:
            raise SystemExit("error: missing canonical routing sections")
        for section in section_paths:
            target = args.home / ".claude/docs/models" / section.name
            desired = section.read_text()
            if read_text(target) != desired:
                changes[target] = desired
        core = (source / CORE_SOURCE).read_text()
        desired_models = MODELS_MARKER + " from routing/core.md; generated, do not edit. -->\n\n" + core
        if models_text and not models_text.startswith(MODELS_MARKER):
            backup = (
                args.home
                / ".claude/ledgers"
                / ("models-handkept-" + hashlib.sha256(models_text.encode()).hexdigest()[:12] + ".md")
            )
            if not backup.exists():
                changes[backup] = models_text
        if desired_models != models_text:
            changes[models_path] = desired_models
        if full_disk.startswith(MODELS_MARKER):
            changes[full_path] = None
        old_policy = args.home / POLICY_DIR / "ROUTING.md"
        pointer = "# Routing reference\n\nRead `~/.claude/docs/models/README.md` and its named sections. This compatibility pointer contains no duplicate policy.\n"
        if not old_policy.exists() and not old_policy.is_symlink():
            changes[old_policy] = pointer
        elif old_policy.is_file() and not old_policy.is_symlink():
            old_text = old_policy.read_text()
            legacy = source / "routing-legacy-sha256.json"
            known = json.loads(legacy.read_text()) if legacy.is_file() else []
            if old_text == pointer:
                pass
            elif hashlib.sha256(old_text.encode()).hexdigest() in known:
                changes[old_policy] = pointer
            else:
                print(f"PRESERVE {old_policy}: modified or unrecognized legacy routing document")

    if nxt != disk_settings:
        changes[settings_path] = nxt_text
    if not args.uninstall and registry_path.parent.is_dir():
        for path in registry_path.parent.glob("registry*.json"):
            legacy = json.loads(path.read_text())
            if (
                isinstance(legacy, dict)
                and legacy.get("rev")
                and isinstance(legacy.get("entries"), dict)
                and registry_rev(legacy["entries"]) == legacy["rev"]
            ):
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
    in_use = {f"registry.{rev}.json" for rev in (folded_rev(nxt), folded_rev(disk_settings)) if rev}
    if registry_path.parent.is_dir():
        for path in registry_path.parent.iterdir():
            if (
                REV_FILE.fullmatch(path.name)
                and path != rev_path
                and path not in changes
                and (
                    args.uninstall
                    or (path.name not in in_use and time.time() - path.stat().st_mtime > REV_KEEP_SECONDS)
                )
            ):
                changes[path] = None
    changes.update(codex_changes(args.home, None if args.uninstall else resolve_sol(source, args.home)))
    preserved_agents, replace_policy_link, policy_link_target = plan_managed_files(
        args, source, snapshot, keep_fold, changes, kept
    )

    retire_hook_registries(args.home, changes, kept)
    validate_hook_changes(args.home, changes, nxt, kept)
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
    if not args.uninstall:
        print("local MCP: " + json.dumps(ensure_harness_mcp(args.home, subprocess.run), sort_keys=True))
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
                shutil.copytree(analytics / name, installed_analytics / name, dirs_exist_ok=True, symlinks=False)
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

    for path, content in sorted(install_staged(args.home, changes).items(), key=write_order):
        if content is None:
            path.unlink()
        else:
            write_atomic(path, content)
            if path.suffix in (".py", ".sh") or path.name in ("verify-routing", "route", "seat"):
                os.chmod(path, path.stat().st_mode | 0o111)
        print(f"{'removed' if content is None else 'updated'} {path}")
    for reason, path in preserved_agents:
        print(f"preserved {reason} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(locked_main(main))
