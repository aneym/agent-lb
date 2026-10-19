#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


def invoke(route: Path, arguments: list[str]) -> tuple[subprocess.CompletedProcess, float]:
    start = time.perf_counter()
    result = subprocess.run([sys.executable, str(route), *arguments], capture_output=True, timeout=60)
    elapsed = (time.perf_counter() - start) * 1000
    if result.returncode:
        raise RuntimeError(
            f"{route} {' '.join(arguments)} exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result, elapsed


def standards(route: Path) -> list[str]:
    configured = os.environ.get("ROUTE_TABLE", "").strip()
    managed = Path.home() / ".agent-lb/managed/coding-agents/routing-table.json"
    repo = route.parent.parent / "config/coding-agents/routing-table.json"
    table_path = Path(configured).expanduser() if configured else managed if managed.is_file() else repo
    table = json.loads(table_path.read_text())
    quota_path = table_path.with_name("quota-swap.json")
    if not quota_path.is_file():
        quota_path = repo.with_name("quota-swap.json")
    if quota_path.is_file():
        for name, spec in json.loads(quota_path.read_text()).get("standards", {}).items():
            table.setdefault("standards", {}).setdefault(name, spec)
    return sorted(table.get("standards", {}))


def timings(route: Path, arguments: list[str], runs: int) -> tuple[float, float]:
    samples = sorted(invoke(route, arguments)[1] for _ in range(runs))
    return statistics.median(samples), samples[max(0, math.ceil(0.9 * runs) - 1)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    route = args.route.expanduser().absolute()
    compare = args.compare.expanduser().absolute() if args.compare else None
    cases = [
        ["resolve", "opus-latest"],
        ["resolve", "sol-latest"],
        *[["resolve", "--json", name] for name in standards(route)],
    ]
    try:
        if compare:
            mismatches = []
            for case in cases:
                before, _ = invoke(compare, case)
                after, _ = invoke(route, case)
                if before.stdout != after.stdout:
                    mismatches.append(" ".join(case))
            if mismatches:
                print("compare: FAIL (stdout mismatch): " + ", ".join(mismatches), flush=True)
                return 1
            print(f"compare: PASS ({len(cases)} resolve cases; identical stdout)", flush=True)
        print(f"runs: {args.runs}; milliseconds; p90: nearest rank", flush=True)
        print("case | before median | before p90 | after median | after p90", flush=True)
        for case in [*cases, ["--help"]]:
            before = timings(compare, case, args.runs) if compare else None
            after = timings(route, case, args.runs)
            columns = f"{before[0]:.1f} | {before[1]:.1f}" if before else "— | —"
            print(f"{' '.join(case)} | {columns} | {after[0]:.1f} | {after[1]:.1f}", flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"route_bench: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
