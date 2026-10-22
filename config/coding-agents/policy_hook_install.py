from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from policy_hook_groups import dispatcher_module  # type: ignore[missing-import]
from policy_inventory import DISPATCH_INPROC  # type: ignore[missing-import]
from policy_layout import DERIVED_PINS, DISPATCH_SCRIPT, MANAGED_AGENTS, SCRIPT_WORD  # type: ignore[missing-import]


def dispatch_worth(source: Path, home: Path) -> Any:

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

        os.environ.pop("LC_ALL", None)
        os.environ.pop("LC_CTYPE", None)
        os.environ.update(HOME=str(home), LANG="en_US.UTF-8")
        try:
            plan = module.plan_hook(hooks[0], dict.fromkeys(DISPATCH_INPROC), script_pins(source))
        except Exception:
            return False
        finally:
            os.environ.clear()
            os.environ.update(saved)
        return plan.kind != "external"

    return worth


def script_pins(source: Path) -> dict[str, str]:

    pins: dict[str, str] = {}
    for name in DERIVED_PINS:
        path = source / "hooks" / name
        if path.is_file():
            pins[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return pins


def managed_hooks(home: Path, source: Path) -> dict[Path, Path]:

    return {
        home / target: source / template
        for target, _owner, _marker, template in MANAGED_AGENTS
        if template.parts[0] == "hooks" and template != Path(DISPATCH_SCRIPT)
    }


def pin_inproc(registry: dict[str, Any], home: Path, source: Path) -> dict[str, Any]:

    pins: dict[str, str] = {}
    managed = {os.path.realpath(target): template for target, template in managed_hooks(home, source).items()}
    for event_entries in registry["entries"].values():
        for hooks in event_entries.values():
            for hook in hooks:
                guarded = dispatcher_module(source).IF_READABLE.match(hook["command"])
                match = SCRIPT_WORD.match(guarded.group("run") if guarded else hook["command"])
                if not match:
                    continue
                word = match.group(1).strip('"')
                if word.startswith("$HOME/"):
                    word = str(home) + word[len("$HOME") :]
                elif word.startswith("~/"):
                    word = str(home) + word[1:]
                path = Path(os.path.realpath(word))
                path = managed.get(str(path), path)
                if path.name in DISPATCH_INPROC and path.is_file():
                    pins[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    pinned = dict(registry)
    pinned["inproc_sha"] = dict(sorted(pins.items()))
    pinned["script_sha"] = script_pins(source)
    pinned["dispatcher_sha256"] = hashlib.sha256((source / DISPATCH_SCRIPT).read_bytes()).hexdigest()
    return pinned
