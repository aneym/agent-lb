from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.modules.pools.schemas import PoolsResponse

_CONFIG = Path(__file__).resolve().parents[3] / "config/coding-agents"


def installed_table_path() -> Path:
    managed = Path.home() / ".agent-lb/managed/coding-agents/routing-table.json"
    return (
        Path(os.environ["ROUTE_TABLE"]).expanduser()
        if os.environ.get("ROUTE_TABLE")
        else (managed if managed.is_file() else _CONFIG / "routing-table.json")
    )


def load_plan_inputs() -> tuple[dict, list[dict]]:
    table_path = installed_table_path()
    table = json.loads(table_path.read_text(encoding="utf-8"))
    costs_path = table_path.with_name("model-costs.json")
    costs = json.loads(
        (costs_path if costs_path.is_file() else _CONFIG / "model-costs.json").read_text(encoding="utf-8")
    )
    if (
        not isinstance(table, dict)
        or not isinstance(costs, list)
        or any(
            not isinstance(row, dict)
            or not all(
                key in row
                for key in (
                    "rung",
                    "model",
                    "harness",
                    "accepted",
                    "of",
                    "minutesPerUnit",
                    "claudePct",
                    "openaiPct",
                    "source",
                )
            )
            or not isinstance(row["minutesPerUnit"], (int, float))
            for row in costs
        )
    ):
        raise ValueError("Invalid account plan inputs")
    return table, costs


def _pool_for(rung: dict, table: dict) -> str:
    pool = rung.get("pool", "")
    if pool.startswith("cursor") or rung.get("seat") == "cursor-seat":
        return "cursor"
    if pool == "devin" or rung.get("seat") == "devin-seat":
        return "devin"
    maker = rung.get("maker") or table.get("seats", {}).get(rung.get("seat"), {}).get("vendor")
    return "openai" if maker == "openai" else "anthropic"


def _entry(rung: dict, table: dict, costs: list[dict]) -> dict:
    pool = _pool_for(rung, table)
    alias = rung.get("model", "")
    model = table.get("aliases", {}).get(alias, {}).get("pinned")
    trial = next((row for row in costs if row["rung"] == rung.get("id")), None)
    if model is None:
        model = (
            trial["model"]
            if trial
            else {
                "sol-latest": "gpt-6.1-sol",
                "sonnet-latest": "claude-sonnet-5-5",
                "opus-latest": "claude-opus-5-5",
                "composer-latest": "composer-2.5",
                "grok-latest": "grok-4.7-medium",
                "grok-latest-low": "grok-4.7-low",
                "swe-latest": "swe-2",
                "swe-latest-medium": "swe-2",
            }.get(alias, alias)
        )
    harness = {"cursor": "Cursor CLI", "devin": "Devin", "openai": "Codex CLI", "anthropic": "Claude Code"}[pool]
    return {"id": rung.get("id", rung.get("seat", alias)), "model": model, "harness": harness}


_WHY = {
    "budget": (
        "Spend least while keeping review quality. Code goes to Cursor first, and you drop the "
        "accounts that sat idle or were slowest."
    ),
    "balanced": (
        "Same accounts as today. Use the pool that would otherwise go unused first, and keep a fallback for every job."
    ),
    "unlimited": (
        "Money is no limit, so buy Claude until it never runs dry, and spend it where Opus is "
        "best: judgment and review. Code still goes to the models that wrote it best in our "
        "trials."
    ),
}
_RISK = {
    "budget": (
        "Risk. When Cursor refuses and the OpenAI plans are low at the same time, code falls "
        "to Claude Sonnet, the pool we guard."
    ),
    "unlimited": (
        "Why not Opus for everything. In our code trial (E12, six units each) Opus medium "
        "finished 4 of 6 and Sol 6 of 6, at almost twice the tokens per finished unit. And "
        "Claude can't review Claude's own code; review must come from another maker."
    ),
}


def _balanced_risk(head: dict, costs: list[dict]) -> str:
    row = next((item for item in costs if item.get("rung") == head.get("id")), None)
    if row is None:
        return "Watch. The first code step has no trial on record."
    line = (
        f"Watch. The first code step passed {row['accepted']} of {row['of']} units "
        f"in its trial ({row['source']})."
    )
    if row["of"] < 20:
        line += " A 20-unit round settles it; if it drops, Sol goes back to first with one switch."
    return line


def build_plan(response: PoolsResponse, table: dict, costs: list[dict]) -> dict[str, Any]:
    pools = {pool.id: pool for pool in response.pools}
    selected = table.get("ladders", {}).get(table.get("ladder"), {})
    jobs = {}
    for job in ("implement", "mechanical", "explore"):
        rungs = selected.get(job)
        if not isinstance(rungs, list):
            rungs = list(table.get("classes", {}).get(job, {}).get("chain", []))
            if job in ("implement", "mechanical"):
                default = table.get("implement_default")
                rungs = [r for r in rungs if r.get("seat") == default] + [r for r in rungs if r.get("seat") != default]
        rungs = [r for r in rungs if "gate" not in r or r["gate"].get("open") is True]

        def demoted(rung):
            return any(
                o.get("class") == job
                and o.get("demote", {}).get("seat") == rung.get("seat")
                and o.get("demote", {}).get("model", rung.get("model")) == rung.get("model")
                for o in table.get("overrides", [])
                if isinstance(o, dict)
            )

        jobs[job] = [r for r in rungs if not demoted(r)] + [r for r in rungs if demoted(r)]
    head = next(iter(jobs["implement"]), {})
    if not head:
        head = next(iter(table.get("classes", {}).get("implement", {}).get("chain", [])), {})
    head_pool = _pool_for(head, table)
    head_maker = head.get("maker") or table.get("seats", {}).get(head.get("seat"), {}).get("vendor")
    verify = selected.get("verify", {})
    review = verify.get("rungs", {})
    review_ids = list(
        dict.fromkeys(
            item.lstrip("@")
            for maker in (head.get("maker", "openai"), "anthropic")
            for item in verify.get("by_author_maker", {}).get(maker, [])[:1]
        )
    )
    jobs["review"] = [{"id": key, **review[key]} for key in review_ids if key in review]
    ids = {"anthropic": "anthropic-general", "openai": "openai-codex", "cursor": "cursor-models", "devin": "devin"}
    labels = {"anthropic": "Claude", "openai": "OpenAI", "cursor": "Cursor Ultra", "devin": "Devin"}
    counts = {vendor: pools[pool_id].accounts if pool_id in pools else 0 for vendor, pool_id in ids.items()}
    cursor_count_pool = pools.get("cursor-models") or pools.get("cursor")
    if cursor_count_pool:
        counts["cursor"] = (
            cursor_count_pool.plan_count if cursor_count_pool.plan_count is not None else cursor_count_pool.accounts
        )
    claude = pools.get("anthropic-general")
    openai = pools.get("openai-codex")
    cursor = pools.get("cursor-models")
    # Claude is never dropped; Unlimited replaces exactly the weekly-empty windows,
    # not accounts temporarily held by a five-hour limit or an auth problem.
    empty = claude.weekly_empty_accounts if claude else 0
    # Devin is dropped only when the trial ranks its rung last on minutes per unit.
    slowest = max((row["minutesPerUnit"] for row in costs), default=0)
    drop_devin = any(row["harness"] == "Devin" and row["minutesPerUnit"] == slowest for row in costs)
    # Cursor's warning is about cycle day, not a fixed calendar date.
    cursor_over = False
    if cursor and cursor.cycle_reset_at and cursor.percent_used is not None:
        reset = cursor.cycle_reset_at
        previous_month = reset.replace(day=1) - timedelta(days=1)
        start = previous_month.replace(day=min(reset.day, previous_month.day))
        day = (response.generated_at - start).total_seconds() / 86400 + 1
        cursor_over = cursor.percent_used > 60 and 1 <= day < 20
    levels = {}
    for level in ("budget", "balanced", "unlimited"):
        accounts = []
        for vendor in ids:
            delta = 0
            reason = "Keep."
            if vendor == "anthropic":
                reason = "Keep. Claude reviews and decides."
                if level == "unlimited" and empty:
                    delta = max(1, empty)
                    reason = f"Add until no week runs dry. {empty} of {counts[vendor]} are out today."
            elif vendor == "openai":
                reason = "Keep. Sol shifts to reviews and reading."
                # Budget drops two OpenAI accounts only when OpenAI is not the maker at the head.
                if level == "budget" and head_maker != "openai":
                    delta = -min(2, max(0, counts[vendor] - 2))
                    reason = (
                        f"Drop {-delta}. Keep {counts[vendor] + delta} for Sol reviews and reading."
                        if delta
                        else f"Keep {counts[vendor]} for Sol reviews and reading."
                    )
                # Unlimited buys one more OpenAI account below 25% aggregate remaining.
                remaining = openai.aggregate_remaining_percent if openai else None
                if level == "unlimited" and remaining is not None and remaining < 25:
                    delta, reason = 1, "Add 1 for Sol reviews of Claude work."
            elif vendor == "cursor":
                reason = "Flag a second plan if Cursor models passes 60% before day 20 of the cycle."
                # Balanced keeps everything, except the already-triggered Cursor warning.
                if level == "balanced" and cursor_over:
                    delta = 1
                elif level == "budget":
                    reason = (
                        f"Keep. {100 - cursor.percent_used:g}% unused."
                        if cursor and cursor.percent_used is not None
                        else "Keep."
                    )
                # Unlimited adds Cursor only when the real implementation head uses Cursor.
                elif level == "unlimited":
                    if head_pool == "cursor":
                        delta, reason = 1, "Add 1 once Grok holds at 20 units."
                    else:
                        reason = "Keep."
            elif vendor == "devin":
                if level == "budget" and drop_devin:
                    delta, reason = -counts[vendor], "Drop all. Slowest in the trial, unused overnight."
                elif level == "balanced":
                    usable = pools["devin"].eligible_accounts if "devin" in pools else 0
                    reason = f"Keep {usable} usable as the last code step; fix or drop the other."
            if not head:
                delta, reason = 0, "head unknown"
            accounts.append(
                {
                    "pool": vendor,
                    "label": labels[vendor],
                    "count": counts[vendor],
                    "change": "add" if delta > 0 else "drop" if delta < 0 else "keep",
                    "delta": delta,
                    "reason": reason,
                }
            )
        # Ladders follow installed routing; Budget removes only entirely dropped pools.
        dropped = {row["pool"] for row in accounts if row["change"] == "drop" and row["count"] + row["delta"] == 0}
        ladders = {
            job: [_entry(rung, table, costs) for rung in rungs if _pool_for(rung, table) not in dropped]
            for job, rungs in jobs.items()
        }
        if level == "unlimited":
            ladders["review"] = [
                {"id": "opus-every-piece", "model": "claude-opus-5-5", "harness": "Claude Code"},
                {"id": "sol-claude-work", "model": "gpt-6.1-sol", "harness": "Codex CLI"},
            ]
        why = _WHY[level]
        if level == "budget" and head_pool != "cursor":
            why = (
                "Spend least while keeping review quality. Keep the installed code head and drop the "
                "accounts that were slowest."
            )
        levels[level] = {
            "title": "Balanced (recommended)" if level == "balanced" else level.title(),
            "why": why,
            "accounts": accounts,
            "ladders": ladders,
            "risk": _balanced_risk(head, costs) if level == "balanced" else _RISK[level],
        }
    return {
        "generatedAt": response.generated_at.isoformat().replace("+00:00", "Z"),
        "recommended": "balanced",
        "levels": levels,
        "costs": deepcopy(costs),
    }
