#!/usr/bin/env python3
"""Live decider eval: does `open-factory route` land on the class default when it should?

Each case runs the real router (live menu, real Jev) with its own ledger, so eval decisions
stay out of ~/.claude/logs/dispatch.jsonl. The receipt goes to ~/.agent-lb/of/runs/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE.parents[1] / "bin" / "open-factory"


def main() -> int:
    cases = json.loads((HERE / "cases.json").read_text())["cases"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path.home() / ".agent-lb" / "of" / "runs" / f"decider-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ROUTE_LEDGER": str(out / "decisions.jsonl")}
    rows = []
    for case in cases:
        proc = subprocess.run(
            [sys.executable, str(CLI), "route", "--class", case["class"], "--json", case["task"]],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        receipt = json.loads(proc.stdout) if proc.returncode == 0 else {}
        default = receipt.get("default")
        expected = default.split(".", 1)[0] if case["expect"] == "default" and default else case["expect"]
        rows.append(
            {
                "class": case["class"],
                "task_head": case["task"][:60],
                "expected": expected,
                "seat": receipt.get("seat"),
                "decider_pick": receipt.get("decider_pick"),
                "default_fit": receipt.get("default_fit"),
                "kept_default": receipt.get("kept_default"),
                "fallback": receipt.get("fallback"),
                "ok": receipt.get("seat") == expected,
                "error": proc.stderr.strip()[:200] if proc.returncode else None,
            }
        )
    passed = sum(r["ok"] for r in rows)
    summary = {"run": out.name, "passed": passed, "total": len(rows), "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    for r in rows:
        mark = "PASS" if r["ok"] else "FAIL"
        print(
            f"{mark} {r['class']:<10} want {r['expected']:<18} got {r['seat']!s:<18} "
            f"jev {r['decider_pick']!s:<34} kept={r['kept_default']} {r['task_head']}"
        )
    print(f"{passed}/{len(rows)} passed; receipt {out / 'summary.json'}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
