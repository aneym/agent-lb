#!/usr/bin/env python3
"""Summarize an of-work run: one Harbor job dir per arm, graded with CIs and agent-lb receipts.

    python3 summarize.py <run-dir> [--reference A2] [--no-receipts]

<run-dir> holds one Harbor job dir per arm, named after the arm. Writes <run-dir>/summary.json
and prints a table. Pass rates carry a task-clustered bootstrap CI (trials of one task are not
independent); arm differences are paired over shared tasks. A trial that errored counts as a
failure and is also counted under `errors`. Receipts come from agent-lb by the agent's own session
id (Claude Code session, Codex rollout), so concurrent trials never mix.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from open_factory.eval.stats import cluster_bootstrap, paired_difference, wilson  # noqa: E402

QUERY = Path.home() / ".agent-lb" / "runtime" / "agent-lb" / "scripts" / "request_log_query.py"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def seconds(span: dict | None) -> float | None:
    if not span or not span.get("started_at") or not span.get("finished_at"):
        return None
    start, end = (datetime.fromisoformat(span[k].replace("Z", "+00:00")) for k in ("started_at", "finished_at"))
    return (end - start).total_seconds()


def session_ids(trial: Path) -> list[str]:
    """Claude Code names its transcript after the session; Codex names its rollout file after it."""
    ids = [p.stem for p in (trial / "agent" / "sessions" / "projects").glob("*/*.jsonl") if UUID.fullmatch(p.stem)]
    for rollout in (trial / "agent" / "sessions").glob("20*/*/*/rollout-*.jsonl"):
        found = UUID.findall(rollout.name)
        ids += found[-1:]
    return sorted(set(ids))


def receipts(ids: list[str]) -> dict:
    total = {"requests": 0, "errors": 0, "cost_usd": 0.0, "input": 0, "cache_read": 0, "output": 0, "accounts": set()}
    for sid in ids:
        proc = subprocess.run(
            [sys.executable, str(QUERY), "--session", sid, "--limit", "5000", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode:
            continue
        for row in json.loads(proc.stdout).get("requests", []):
            total["requests"] += 1
            total["errors"] += row.get("status") != "success"
            total["cost_usd"] += row.get("cost_usd") or 0
            total["input"] += row.get("input_tokens") or 0
            total["cache_read"] += row.get("cache_read") or 0
            total["output"] += row.get("output_tokens") or 0
            if row.get("account_id"):
                total["accounts"].add(row["account_id"])
    total["accounts"] = len(total["accounts"])
    return total


def trials(job: Path, with_receipts: bool) -> list[dict]:
    rows = []
    for path in sorted(job.glob("*/result.json")):
        result = json.loads(path.read_text())
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        agent = result.get("agent_result") or {}
        row = {
            "task": result["task_name"].split("/")[-1],
            "trial": result["trial_name"],
            "reward": float(rewards.get("reward") or 0.0),
            "error": (result.get("exception_info") or {}).get("exception_type"),
            "agent_s": seconds(result.get("agent_execution")),
            "harbor_input": agent.get("n_input_tokens"),
            "harbor_cache": agent.get("n_cache_tokens"),
            "harbor_output": agent.get("n_output_tokens"),
        }
        if with_receipts:
            row["sessions"] = session_ids(path.parent)
            row["lb"] = receipts(row["sessions"])
        rows.append(row)
    return rows


def mean(values: list) -> float | None:
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("--reference")
    parser.add_argument("--no-receipts", action="store_true")
    args = parser.parse_args()

    arms = {job.name: trials(job, not args.no_receipts) for job in sorted(args.run.iterdir()) if job.is_dir()}
    by_task = {arm: defaultdict(list) for arm in arms}
    summary: dict = {"run": args.run.name, "arms": {}, "differences": {}}
    for arm, rows in arms.items():
        for row in rows:
            by_task[arm][row["task"]].append(row["reward"])
        passes = sum(r["reward"] >= 1.0 for r in rows)
        lb = [r["lb"] for r in rows if "lb" in r]
        summary["arms"][arm] = {
            "trials": len(rows),
            "tasks": len(by_task[arm]),
            "errors": sum(bool(r["error"]) for r in rows),
            "pass_rate": cluster_bootstrap(dict(by_task[arm]), lambda xs: sum(xs) / len(xs)),
            "pass_rate_wilson": wilson(passes, len(rows)) if rows else None,
            "agent_s_mean": mean([r["agent_s"] for r in rows]),
            "lb_cost_usd_mean": mean([x["cost_usd"] for x in lb]),
            "lb_input_mean": mean([x["input"] for x in lb]),
            "lb_cache_read_mean": mean([x["cache_read"] for x in lb]),
            "lb_output_mean": mean([x["output"] for x in lb]),
            "lb_requests_mean": mean([x["requests"] for x in lb]),
            "trials_without_receipts": sum(1 for x in lb if not x["requests"]),
        }
    reference = args.reference or next(iter(arms), None)
    for arm in arms:
        if arm != reference and reference in arms and set(by_task[arm]) & set(by_task[reference]):
            summary["differences"][f"{arm} - {reference}"] = paired_difference(
                dict(by_task[arm]), dict(by_task[reference])
            )
    (args.run / "summary.json").write_text(json.dumps({**summary, "trials": arms}, indent=2, default=str) + "\n")

    print(f"{'arm':<14}{'trials':>7}{'err':>5}  pass rate [95% CI]      agent s   lb $/trial  cache-read")
    for arm, s in summary["arms"].items():
        p = s["pass_rate"]
        cache = (
            s["lb_cache_read_mean"] / max(1, s["lb_input_mean"] + s["lb_cache_read_mean"])
            if s["lb_input_mean"] is not None
            else None
        )
        print(
            f"{arm:<14}{s['trials']:>7}{s['errors']:>5}  {p['estimate']:.2f} [{p['low']:.2f}, {p['high']:.2f}]"
            f"{(s['agent_s_mean'] or 0):>12.0f}{(s['lb_cost_usd_mean'] or 0):>12.3f}"
            f"{'' if cache is None else f'{cache:>11.0%}'}"
        )
    for name, d in summary["differences"].items():
        print(f"{name}: {d['estimate']:+.2f} [{d['low']:+.2f}, {d['high']:+.2f}] over {d['tasks']} tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
