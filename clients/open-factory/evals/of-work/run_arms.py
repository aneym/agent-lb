#!/usr/bin/env python3
"""Run of-work arms as concurrent Harbor jobs, one job dir per arm, then summarize.

    python3 run_arms.py <run-name> --arms A2-cc-sonnet,A4-codex-sol [-k 1] [-n 3] [--tasks DIR]

Writes ~/.agent-lb/of/runs/<run-name>/<arm>/ and <run-name>/summary.json. Check `route pools`
first: the arms spend the pooled subscriptions.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARBOR_DIR = HERE.parents[1] / "harbor"
RUNS = Path.home() / ".agent-lb" / "of" / "runs"
TASKS = Path.home() / ".agent-lb" / "of" / "dataset" / "v0" / "tasks"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run")
    parser.add_argument("--arms", required=True)
    parser.add_argument("-k", type=int, default=1, help="attempts per task")
    parser.add_argument("-n", type=int, default=3, help="concurrent trials per arm")
    parser.add_argument("--tasks", type=Path, default=TASKS)
    parser.add_argument("--include", action="append", default=[], help="Harbor -i task filter")
    args = parser.parse_args()

    config = json.loads((HERE / "arms.json").read_text())["arms"]
    out = RUNS / args.run
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(HARBOR_DIR)}
    procs = []
    for arm in args.arms.split(","):
        spec = config[arm]
        cmd = [
            "harbor",
            "run",
            "-p",
            str(args.tasks),
            "-a",
            spec["agent"],
            "-m",
            spec["model"],
            "-k",
            str(args.k),
            "-n",
            str(args.n),
            "-o",
            str(out),
            "--job-name",
            arm,
        ]
        for pattern in args.include:
            cmd += ["-i", pattern]
        log = (out / f"{arm}.log").open("w")
        procs.append((arm, subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)))
        print(f"started {arm}: {' '.join(cmd[2:])}")
    failed = [arm for arm, proc in procs if proc.wait() != 0]
    for arm in failed:
        print(f"{arm}: harbor exited non-zero; see {out / (arm + '.log')}")
    return subprocess.call([sys.executable, str(HERE / "summarize.py"), str(out)])


if __name__ == "__main__":
    raise SystemExit(main())
