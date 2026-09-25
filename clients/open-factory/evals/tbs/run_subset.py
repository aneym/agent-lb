#!/usr/bin/env python3
"""Terminal-Bench-Science on a fixed seeded subset, through our seats and agent-lb.

The public comparison dataset (PROGRAM.md D7 (2)). Artificial Analysis publishes pass@1 on the
full 70 tasks; we run a seeded subset, one attempt per task, and report it as a subset.

    python3 run_subset.py --arm A2-cc-sonnet [--tasks 10] [--seed 0] [-n 4] [--dry-run]

- Local docker only (no Modal/Daytona), at most 4 concurrent trials: each task asks for up to
  4 CPUs and 16 GB, and Studio is shared.
- The images are the dataset's own, without our prebaked CLIs, so the agents install them
  (OF_HARBOR_ALLOW_INSTALL=1). The tasks use open egress: they are not our code, so the leak
  rule does not apply.
- Agent timeouts are the dataset's (8 h), as in the published runs.
- --dry-run checks the dataset, the subset and the arm, prints the harbor command, and runs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARBOR_DIR = HERE.parents[1] / "harbor"
ARMS = HERE.parent / "of-work" / "arms.json"
DATASET = Path.home() / ".agent-lb" / "of" / "public" / "tbs" / "terminal-bench-science"
RUNS = Path.home() / ".agent-lb" / "of" / "runs"
MAX_CONCURRENT = 4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", required=True)
    parser.add_argument("--tasks", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("-n", type=int, default=MAX_CONCURRENT)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dataset.is_dir():
        print(f"missing {args.dataset}: harbor datasets download terminal-bench-science/terminal-bench-science@latest")
        return 1
    arm = json.loads(ARMS.read_text())["arms"][args.arm]
    names = sorted(p.name for p in args.dataset.iterdir() if (p / "task.toml").is_file())
    subset = sorted(random.Random(args.seed).sample(names, args.tasks))
    n = min(args.n, MAX_CONCURRENT)
    run = RUNS / f"tbs-s{args.seed}-{args.tasks}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    cmd = [
        "harbor",
        "run",
        "-p",
        str(args.dataset),
        "-a",
        arm["agent"],
        "-m",
        arm["model"],
        "-k",
        "1",
        "-n",
        str(n),
        "-o",
        str(run),
        "--job-name",
        args.arm,
    ]
    for name in subset:
        # Harbor matches -i against the task name, bare for a local dir, org/name from the registry.
        cmd += ["-i", name, "-i", f"*/{name}"]

    print(f"dataset {args.dataset.name}: {len(names)} tasks; subset seed={args.seed}: {len(subset)}")
    for name in subset:
        env = tomllib.loads((args.dataset / name / "task.toml").read_text()).get("environment", {})
        print(f"  {name:<40} cpus={env.get('cpus')} mem={env.get('memory_mb')} net={env.get('network_mode')}")
    print("OF_HARBOR_ALLOW_INSTALL=1 PYTHONPATH=" + str(HARBOR_DIR), *cmd)
    if args.dry_run:
        return 0
    run.mkdir(parents=True)
    (run / "subset.json").write_text(json.dumps({"seed": args.seed, "tasks": subset, "arm": args.arm, **arm}, indent=1))
    env = {**os.environ, "PYTHONPATH": str(HARBOR_DIR), "OF_HARBOR_ALLOW_INSTALL": "1"}
    with (run / f"{args.arm}.log").open("w") as log:
        code = subprocess.call(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    summarize = HERE.parent / "of-work" / "summarize.py"
    return code or subprocess.call([sys.executable, str(summarize), str(run)])


if __name__ == "__main__":
    raise SystemExit(main())
