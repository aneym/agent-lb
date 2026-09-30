#!/usr/bin/env python3
"""Force a 429 and recovery through the isolated ASGI LB harness, never a live LB."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="stand-in-forced-swap-") as scratch:
        env = {**os.environ, "AGENT_LB_TEST_DATABASE_URL": f"sqlite+aiosqlite:///{scratch}/lb.db"}
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit/test_stand_in_widening.py::"
             "test_stand_in_forced_swap_and_recovery[build]", "-q"],
            cwd=repo, env=env, capture_output=True, text=True, timeout=60,
        )
    if result.returncode:
        print("FAIL forced swap/recovery harness", file=sys.stderr)
        print(result.stdout + result.stderr, file=sys.stderr, end="")
        return 1
    print("PASS 429: tagged main thread answered by sol-latest-high with swap header")
    print("PASS recovery: next request answered by Anthropic without swap header")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL isolated harness: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
