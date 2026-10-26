#!/usr/bin/env python3
import importlib.util
import json
import sys
from pathlib import Path

from hook_current_settings import current_entry  # type: ignore[missing-import]


def main():
    path = Path(__file__).resolve().with_name("hook-dispatch.py")
    spec = importlib.util.spec_from_file_location("cached_seat_dispatch", path)
    if spec is None or spec.loader is None:
        return 2
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = sys.stdin.buffer.read()
    try:
        tool = json.loads(payload).get("tool_name") or "Agent"
        current = current_entry("PreToolUse", tool)
        if current is None:
            registry, hooks, _ = module.load_entry("PreToolUse", tool)
            current = registry, hooks, None
        result = module.run_entry("PreToolUse", current[1], payload, {}, tool)
    except (OSError, ValueError) as exc:
        result = module.refusal("PreToolUse", str(exc))
    return module.answer(result)


if __name__ == "__main__":
    raise SystemExit(main())
