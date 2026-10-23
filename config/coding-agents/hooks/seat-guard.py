#!/usr/bin/env python3
import importlib.util
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
    try:
        current = current_entry("PreToolUse", "Agent")
        if current is None:
            raise ValueError("current settings still use the dispatcher")
        result = module.run_entry("PreToolUse", current[1], sys.stdin.buffer.read(), {}, "Agent")
    except (OSError, ValueError) as exc:
        result = module.refusal("PreToolUse", str(exc))
    return module.answer(result)


if __name__ == "__main__":
    raise SystemExit(main())
