from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from route_learning_text import learning_outcomes, learning_text  # pyrefly: ignore [missing-import]
from route_probe_model import eval_gate  # pyrefly: ignore [missing-import]
from route_seat_states import (  # pyrefly: ignore [missing-import]
    EVAL_MDD,
    arm_key,
    arm_paused,
    attributed_outcome,
    learning_arms,
    learning_evidence,
    learning_metrics,
    learning_window,
    load_eval_standings,
)
from route_storage import atomic_write  # pyrefly: ignore [missing-import]
from route_versioned_rules import EXIT_OK, fail, load_table  # pyrefly: ignore [missing-import]


def learning_report(
    table: dict[str, Any], window: str, only: str | None = None, source: str = "prod"
) -> dict[str, Any]:
    since, until = learning_window(window)
    outcomes = learning_outcomes(since, until, source)
    standings = load_eval_standings()
    standards = table.get("standards", {})
    classes = {}
    proposals = []
    vetoed = []
    minimum = table.get("explore", {}).get("min_n_per_arm", 40)
    if not isinstance(minimum, int) or minimum < 1:
        fail("min_n_per_arm must be positive")
    for class_name, default in standards.items():
        if only and class_name != only:
            continue
        default_id = arm_key(default)
        class_rows = [row for row in outcomes if row.get("class") == class_name]
        valid = [row for row in class_rows if attributed_outcome(row)]
        arms = []
        for arm in [default, *learning_arms(table, class_name)]:
            arm_id = arm_key(arm)
            if not arm_id or any(item["arm_id"] == arm_id for item in arms):
                continue
            eligible = [
                row
                for row in valid
                if default_id in row.get("eligible_arms", []) and arm_id in row.get("eligible_arms", [])
            ]
            selected = [row for row in eligible if row.get("arm_id") == arm_id]
            total = sum(row.get("arm_id") == arm_id for row in class_rows)
            metrics = learning_metrics(selected, total)
            paused = arm_paused(table, class_name, arm_id) or arm.get("paused", False)
            gate = None if arm_id == default_id else eval_gate(class_name, arm_id, default_id, standings)
            arms.append(
                {
                    "arm_id": arm_id,
                    "default": arm_id == default_id,
                    **metrics,
                    "paused": bool(paused),
                    "eval_gate": gate,
                }
            )
            if arm_id == default_id:
                continue
            baseline = learning_metrics([row for row in eligible if row.get("arm_id") == default_id], len(eligible))
            if (
                source == "prod"
                and metrics["n"] >= minimum
                and baseline["n"] >= minimum
                and metrics["interval"][0] >= baseline["accept_rate"] - 0.05
                and metrics["cost_per_accepted"] is not None
                and baseline["cost_per_accepted"] is not None
                and metrics["cost_per_accepted"] < baseline["cost_per_accepted"]
                and not paused
            ):
                proposal = {
                    "class": class_name,
                    "action": "promote",
                    "arm_id": arm_id,
                    "evidence": learning_evidence(metrics, window, table),
                    "eval_gate": gate,
                }
                (vetoed if gate["veto"] else proposals).append(proposal)  # pyrefly: ignore [unsupported-operation]
        classes[class_name] = {"default": default_id, "arms": arms}
    report = {
        "window": window,
        "source": source,
        "policy_rev": table.get("policy_rev"),
        "classes": classes,
        "proposals": proposals,
        "vetoed": vetoed,
        "pending_alex": [
            entry
            for entry in table.get("overrides", [])
            if isinstance(entry, dict)
            and entry.get("pending_alex")
            and not entry.get("reverted_at")
            and (not only or entry.get("class") == only)
        ],
    }
    if source != "prod":
        report["mdd"] = EVAL_MDD
    return report


def command_standings(args: argparse.Namespace) -> int:
    report = learning_report(load_table(apply_default=False), args.since, args.task_class, args.source)
    if args.mdx:
        atomic_write(Path(args.mdx).expanduser(), learning_text(report))
    print(json.dumps(report, indent=2) if args.json else learning_text(report))
    return EXIT_OK
