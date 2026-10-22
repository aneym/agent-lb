from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from route_class_candidates import outcomes_path  # pyrefly: ignore [missing-import]
from route_storage import atomic_write  # pyrefly: ignore [missing-import]
from route_versioned_rules import fail, parse_ts, table_path  # pyrefly: ignore [missing-import]


def learning_outcomes(since: datetime, until: datetime, source: str = "prod") -> list[dict[str, Any]]:
    try:
        lines = outcomes_path().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = {}
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if source != "all" and (row.get("source") == "eval") != (source == "eval"):
            continue
        stamp = parse_ts(row.get("ts"))
        if stamp is None or stamp.tzinfo is None or not since <= stamp <= until:
            continue
        job = row.get("job_id")
        if not isinstance(job, str) or not job:
            continue
        if job not in records or stamp >= records[job][0]:
            records[job] = (stamp, row)
    return [row for _, row in sorted(records.values(), key=lambda item: item[0])]


def write_learning_table(table: dict[str, Any]) -> None:
    canonical = Path(__file__).resolve().parent.parent / "config/coding-agents/routing-table.json"
    if table_path().resolve() == canonical:
        fail("learning writes require a managed routing table, set ROUTE_TABLE to its path")
    atomic_write(table_path(), json.dumps(table, indent=2) + "\n")


def learning_text(report: dict[str, Any]) -> str:
    lines = ["---", 'title: "Routing standings"', "---", "", "# Routing standings", ""]
    for class_name, data in report["classes"].items():
        lines.extend(
            [
                f"## {class_name}",
                "",
                "| Arm | Default | n | Attributed share | Acceptance (90% interval) | "
                "$/accepted | Median wall s | Paused |",
                "|---|---|---:|---:|---|---:|---:|---|",
            ]
        )
        for arm in data["arms"]:
            cost = arm["cost_per_accepted"]
            rate = arm["accept_rate"]
            low, high = arm["interval"]
            acceptance = f"{rate:.1%} ({low:.1%} to {high:.1%})" if rate is not None else "unknown"
            price = f"{cost:.4f}" if cost is not None else "cost unknown"
            lines.append(
                f"| {arm['arm_id']} | {arm['default']} | {arm['n']} | {arm['attributed_share']:.1%} | "
                f"{acceptance} | {price} | {arm['median_wall_s']} | {arm['paused']} |"
            )
        lines.append("")
    for proposal in report["proposals"]:
        lines.append(f"Propose promoting {proposal['arm_id']} for {proposal['class']}.")
    for proposal in report.get("vetoed", []):
        lines.append(
            f"Eval gate vetoed promoting {proposal['arm_id']} for {proposal['class']}: "
            f"{proposal['eval_gate']['reason']}."
        )
    if report.get("mdd"):
        lines.append(report["mdd"])
    for entry in report["pending_alex"]:
        lines.append(f"Alex's approval is pending for {entry['class']} promotion to {entry['arm_id']}.")
    if not report["proposals"]:
        lines.append("No promotions proposed.")
    return "\n".join(lines) + "\n"
