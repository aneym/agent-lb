#!/usr/bin/env python3
"""Recompute each task's required tests from in-container oracle and nop runs.

The miner recorded FAIL_TO_PASS/PASS_TO_PASS on the host. Inside the task image some of those
tests behave differently (no system tools, other Python), so the lists are re-derived where the
agent will be graded, the way SWE-bench derives them:

- FAIL_TO_PASS: passes with the fix (oracle), does not pass without it (nop).
- PASS_TO_PASS: passes in both.

A task with no FAIL_TO_PASS left, or whose oracle trial did not finish, is moved to
<tasks-dir>/../dropped/ so arm runs never see it. The host lists are kept as tests/required.host.json.

    python3 calibrate.py <tasks-dir> <oracle-job-dir> <nop-job-dir>
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def outcomes_by_task(job: Path) -> dict[str, dict[str, str] | None]:
    found: dict[str, dict[str, str] | None] = {}
    for trial in sorted(p for p in job.iterdir() if p.is_dir() and "__" in p.name):
        path = trial / "verifier" / "outcomes.json"
        found[trial.name.split("__", 1)[0]] = json.loads(path.read_text()) if path.is_file() else None
    return found


def main() -> int:
    tasks, oracle_job, nop_job = (Path(arg) for arg in sys.argv[1:4])
    oracle, nop = outcomes_by_task(oracle_job), outcomes_by_task(nop_job)
    report = []
    for task in sorted(p for p in tasks.iterdir() if (p / "task.toml").is_file()):
        with_fix, without = oracle.get(task.name), nop.get(task.name)
        required_path = task / "tests" / "required.json"
        host_path = task / "tests" / "required.host.json"
        if not host_path.exists():
            host_path.write_text(required_path.read_text())
        host = json.loads(host_path.read_text())
        if with_fix is None or without is None:
            report.append({"id": task.name, "dropped": "oracle or nop trial did not grade"})
            continue
        f2p = sorted(n for n, o in with_fix.items() if o == "PASSED" and without.get(n) != "PASSED")
        p2p = sorted(n for n, o in with_fix.items() if o == "PASSED" and without.get(n) == "PASSED")
        host_f2p = set(host["fail_to_pass"])
        row = {
            "id": task.name,
            "fail_to_pass": len(f2p),
            "pass_to_pass": len(p2p),
            "host_f2p_kept": len(host_f2p & set(f2p)),
            "host_f2p": len(host_f2p),
        }
        if not f2p:
            row["dropped"] = "no test fails without the fix and passes with it in the image"
        else:
            required_path.write_text(json.dumps({"fail_to_pass": f2p, "pass_to_pass": p2p}, indent=1) + "\n")
        report.append(row)
    dropped_dir = tasks.parent / "dropped"
    for row in report:
        if "dropped" in row:
            dropped_dir.mkdir(exist_ok=True)
            shutil.move(str(tasks / row["id"]), str(dropped_dir / row["id"]))
    (tasks / "calibration.json").write_text(json.dumps(report, indent=2) + "\n")
    kept = [r for r in report if "dropped" not in r]
    print(f"kept {len(kept)} of {len(report)} tasks")
    for r in report:
        if "dropped" in r:
            print(f"  dropped {r['id']}: {r['dropped']}")
        elif r["host_f2p_kept"] < r["host_f2p"]:
            print(f"  {r['id']}: {r['host_f2p_kept']}/{r['host_f2p']} host FAIL_TO_PASS hold in the image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
