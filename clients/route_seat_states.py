from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from route_anthropic_models import family_alias, http_get, percent  # pyrefly: ignore [missing-import]
from route_reserve_state import pool_for_model, utc_stamp  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    ANALYTICS_SESSION_CAP,
    MESSAGE_HEAD_CHARS,
    SEAT_PROBE,
    SESSION_WINDOW_MINUTES,
    fail,
    ledger_path,
    parse_ts,
)


def pinned_model(table: dict[str, Any], alias: str | None) -> str | None:
    if not alias:
        return None
    spec = (table.get("aliases") or {}).get(family_alias(table, alias))
    pinned = spec.get("pinned") if isinstance(spec, dict) else None
    return pinned if isinstance(pinned, str) else None


REQUEST_LOG_PAGE = 200
REQUEST_LOG_CAP = 1000
ERROR_STATUSES = "&status=rate_limit&status=quota&status=error"


def api_request_logs(since: datetime, *, cache: bool = False, errors_only: bool = False) -> list[dict[str, Any]]:
    if cache:
        since = since.replace(second=since.second // 5 * 5, microsecond=0)
    until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    if cache:
        until = until.replace(second=until.second // 5 * 5) + timedelta(seconds=5)
    query = f"includeTotal=false&since={utc_stamp(since)}&until={utc_stamp(until)}"
    query += ERROR_STATUSES if errors_only else ""
    rows: list[dict[str, Any]] = []
    while len(rows) < REQUEST_LOG_CAP:
        result = http_get(f"/api/request-logs?limit={REQUEST_LOG_PAGE}&offset={len(rows)}&{query}")
        body = result.body if result.ok and isinstance(result.body, dict) else {}
        page = [row for row in body.get("requests") or [] if isinstance(row, dict)]
        rows.extend(page)
        if len(page) < REQUEST_LOG_PAGE:
            break
    return rows[:REQUEST_LOG_CAP]


def message_head(body: Any) -> str | None:

    if not isinstance(body, dict):
        return None
    error = body.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str) or not message.strip():
        return None
    return " ".join(message.split())[:MESSAGE_HEAD_CHARS]


def recent_model_traffic(*, window: int = SESSION_WINDOW_MINUTES) -> dict[str, dict[str, Any]]:

    result = http_get(f"/api/sessions?windowMinutes={window}&limit=200", timeout=4.0)
    traffic: dict[str, dict[str, Any]] = {}
    if not result.json_ok or not isinstance(result.body, dict):
        return traffic
    for session in result.body.get("sessions") or []:
        if not isinstance(session, dict):
            continue
        errors = session.get("errors")
        errors = int(errors) if isinstance(errors, (int, float)) and not isinstance(errors, bool) else 0
        for entry in session.get("models") or []:
            if not isinstance(entry, dict):
                continue
            model, requests = entry.get("model"), entry.get("requests")
            if not isinstance(model, str) or isinstance(requests, bool) or not isinstance(requests, (int, float)):
                continue
            requests = int(requests)
            if requests <= 0 or requests <= errors:
                continue
            candidate = {
                "requests": requests,
                "errors": errors,
                "clean": errors == 0,
                "lastSeen": session.get("lastSeen"),
            }
            current = traffic.get(model)
            if current is None or (candidate["clean"] and not current["clean"]) or requests > current["requests"]:
                traffic[model] = candidate
    return traffic


def pool_verdict(
    model: str, table: dict[str, Any], pools: dict[str, Any], *, note: str, latency_ms: int | None
) -> dict[str, Any]:

    pool = pool_for_model(table, model)
    status = pools.get(pool, {}).get("status") if pool else None
    ok = status != "exhausted"
    return {
        "name": f"model:{model}",
        "ok": ok,
        "latency_ms": latency_ms,
        "error": f"pool {pool} exhausted" if not ok else None,
        "evidence": "pool_status",
        "note": note,
        "pool": pool,
        "poolStatus": status,
    }


def probed_models(table: dict[str, Any]) -> list[str]:

    seen: list[str] = []
    classes = table.get("classes")
    if not isinstance(classes, dict):
        return seen
    for spec in classes.values():
        if not isinstance(spec, dict):
            continue
        for entry in spec.get("chain") or []:
            model = entry.get("model") if isinstance(entry, dict) else None
            if not isinstance(model, str) or not model.startswith("claude-"):
                continue
            if "fable" in model or pool_for_model(table, model) == "anthropic-fable":
                continue
            if model not in seen:
                seen.append(model)
    return seen


def backing_probe(table: dict[str, Any], seat: str, model: str, models: list[str]) -> str:
    if seat in SEAT_PROBE:
        return SEAT_PROBE[seat]
    if model.startswith("cursor-"):
        return "cursor-agent"
    alias = (table.get("aliases") or {}).get(model) if isinstance(table.get("aliases"), dict) else None
    if model.startswith("gpt-") or (isinstance(alias, dict) and alias.get("vendor") == "openai"):
        return "codex-companion"
    if model in models:
        return f"model:{model}"
    return "lb-health"


def seat_states(table: dict[str, Any], probes: dict[str, dict[str, Any]], models: list[str]) -> dict[str, Any]:
    seats: dict[str, Any] = {}
    classes = table.get("classes")
    if not isinstance(classes, dict):
        return seats
    for spec in classes.values():
        if not isinstance(spec, dict):
            continue
        for entry in spec.get("chain") or []:
            if not isinstance(entry, dict):
                continue
            seat, model = entry.get("seat"), entry.get("model")
            if not isinstance(seat, str) or not isinstance(model, str):
                continue
            probe = probes.get(backing_probe(table, seat, model, models))
            if probe is None:
                continue
            record = {
                "ok": probe["ok"],
                "latency_ms": probe.get("latency_ms"),
                "error": probe.get("error"),
                "evidence": probe.get("evidence", "probe"),
            }
            if probe.get("note"):
                record["note"] = probe["note"]
            current = seats.get(seat)
            if current is None or (current["ok"] and not probe["ok"]):
                seats[seat] = record
            elif current["ok"] == probe["ok"]:
                latencies = [value for value in (current["latency_ms"], probe.get("latency_ms")) if value is not None]
                current["latency_ms"] = max(latencies) if latencies else None
    return seats


def alert_text(state: dict[str, Any]) -> str:
    down = [f"{name} ({record['error']})" for name, record in sorted(state["seats"].items()) if not record["ok"]]
    broken = [f"{probe['name']} ({probe['error']})" for probe in state["probes"] if not probe["ok"]]
    pools = state.get("pools") or {}
    thin = [
        f"{pool.get('id')} {percent(pool.get('headroomPercent'))} ({pool.get('status')})"
        for pool in (pools.get("pools") or [])
        if pool.get("status") in ("low", "exhausted")
    ]
    lines = [
        f"ROUTING DEGRADED at {state['ts']}",
        f"failed probes : {', '.join(broken) or 'none'}",
        f"seats down    : {', '.join(down) or 'none'}",
        f"pools thin    : {', '.join(thin) or 'none'}",
        "",
        "`route pick <class>` already routes around this. Re-run `route doctor --write` to clear.",
    ]
    return "\n".join(lines) + "\n"


def read_ledger(since: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dispatches: list[dict[str, Any]] = []
    closeouts: list[dict[str, Any]] = []
    try:
        lines = ledger_path().read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return dispatches, closeouts
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        stamp = parse_ts(record.get("ts"))
        if stamp is None or stamp < since:
            continue
        if record.get("event") == "dispatch":
            dispatches.append(record)
        elif record.get("event") == "closeout":
            closeouts.append(record)
    return dispatches, closeouts


def session_costs(session_ids: set[str]) -> dict[str, float]:
    costs: dict[str, float] = {}
    for session_id in sorted(session_ids)[:ANALYTICS_SESSION_CAP]:
        result = http_get(f"/api/sessions/{session_id}/analytics", timeout=4.0)
        if not result.json_ok or not isinstance(result.body, dict):
            continue
        session = result.body.get("session")
        cost = session.get("costUsd") if isinstance(session, dict) else None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            costs[session_id] = float(cost)
    return costs


def ok_rate(row: dict[str, Any]) -> float | None:
    return row["ok"] / row["closeouts"] if row["closeouts"] else None


def learning_window(value: str) -> tuple[datetime, datetime]:
    match = re.fullmatch(r"([1-9][0-9]*)d", value)
    if not match:
        fail("--since must be a positive number of days, for example 30d")
    until = datetime.now(timezone.utc)
    return until - timedelta(days=int(match[1])), until  # pyrefly: ignore [unsupported-operation]


def arm_key(arm: dict[str, Any]) -> str:
    return str(arm.get("arm_id") or arm.get("id") or "")


def learning_arms(table: dict[str, Any], class_name: str) -> list[dict[str, Any]]:
    arms = table.get("explore", {}).get("arms", {}).get(class_name, [])
    return arms if isinstance(arms, list) else []


def attributed_outcome(row: dict[str, Any]) -> bool:
    propensity = row.get("propensity")
    return (
        row.get("attribution_complete") is True
        and row.get("outcome") not in ("infra_failure", "pending")
        and isinstance(row.get("outcome"), str)
        and isinstance(propensity, (float, int))
        and not isinstance(propensity, bool)
        and math.isfinite(propensity)
        and 0 < propensity <= 1
    )


def wilson_interval(rate: float, weight: float) -> list[float]:
    if not weight:
        return [0.0, 1.0]
    z = 1.6448536269514722
    denominator = 1 + z * z / weight
    center = (rate + z * z / (2 * weight)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / weight + z * z / (4 * weight * weight)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def learning_metrics(rows: list[dict[str, Any]], total: int) -> dict[str, Any]:
    import statistics

    weight = sum(1 / row["propensity"] for row in rows)
    accepted_weight = sum(1 / row["propensity"] for row in rows if row["outcome"] == "accepted")
    rate = accepted_weight / weight if weight else 0.0
    known = [
        row
        for row in rows
        if isinstance(row.get("cost_usd"), (float, int))
        and not isinstance(row["cost_usd"], bool)
        and math.isfinite(row["cost_usd"])
        and row["cost_usd"] >= 0
    ]
    known_weight = sum(1 / row["propensity"] for row in known)
    known_accepted = sum(1 / row["propensity"] for row in known if row["outcome"] == "accepted")
    walls = [
        row["wall_s"]
        for row in rows
        if isinstance(row.get("wall_s"), (float, int))
        and not isinstance(row["wall_s"], bool)
        and math.isfinite(row["wall_s"])
        and row["wall_s"] >= 0
    ]
    coverage = len(known) / len(rows) if rows else 0.0
    cost = (
        sum(row["cost_usd"] / row["propensity"] for row in known) / known_accepted
        if known_accepted and coverage >= 0.8 and known_weight / weight >= 0.8
        else None
    )
    return {
        "n": len(rows),
        "accepted": sum(row["outcome"] == "accepted" for row in rows),
        "attributed_share": len(rows) / total if total else 0.0,
        "weighted_n": weight,
        "accept_rate": rate if rows else None,
        "interval": wilson_interval(rate, weight),
        "cost_known_share": coverage,
        "cost_per_accepted": cost,
        "median_wall_s": statistics.median(walls) if walls else None,
    }


def v2_override(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    evidence = entry.get("evidence")
    return (
        isinstance(evidence, dict)
        and {"n", "accepted", "lower_bound", "cost_per_accepted", "window", "policy_rev"} <= evidence.keys()
        and {"class", "action", "arm_id", "reason", "ts", "id"} <= entry.keys()
    )


def arm_paused(table: dict[str, Any], class_name: str, arm_id: str) -> bool:
    return any(
        entry.get("class") == class_name
        and entry.get("arm_id") == arm_id
        and entry.get("action") == "pause"
        and not entry.get("reverted_at")
        for entry in table.get("overrides", [])
        if isinstance(entry, dict)
    )


EVAL_MDD = "MDD ~30pp per-trial at 30 trials/arm (alpha .1 one-sided, power .8)"

EVAL_ARM_MAP = {"sol-med": "implement standard", "sol-low": "mechanical standard"}


def eval_standings_path() -> Path:
    return Path(
        os.environ.get("ROUTE_EVAL_STANDINGS") or Path.home() / ".agent-lb/audits/routing/eval-standings.json"
    ).expanduser()


def load_eval_standings() -> dict[str, Any]:
    try:
        data = json.loads(eval_standings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def eval_arm(standings: dict[str, Any], class_name: str, arm_id: str, default_arm: str) -> dict[str, Any] | None:
    mapping = standings.get("_map") if isinstance(standings.get("_map"), dict) else EVAL_ARM_MAP
    targets = {f"{class_name} {arm_id}"} | ({f"{class_name} standard"} if arm_id == default_arm else set())
    names = [name for name, target in mapping.items() if target in targets]
    if not names and isinstance(standings.get(arm_id), dict) and standings[arm_id].get("class") == class_name:
        names = [arm_id]
    for name in names:
        entry = standings.get(name)
        tasks = entry.get("tasks") if isinstance(entry, dict) else None
        if not isinstance(tasks, dict):
            continue
        runs = sum(int(task.get("runs") or 0) for task in tasks.values() if isinstance(task, dict))
        passes = sum(int(task.get("passes") or 0) for task in tasks.values() if isinstance(task, dict))
        if not runs:
            continue
        rate = entry.get("per_trial_rate")  # pyrefly: ignore [missing-attribute]
        if not isinstance(rate, (float, int)) or isinstance(rate, bool):
            rate = passes / runs
        return {
            "eval_arm": name,
            "task_version_set": sorted(entry.get("task_version_set") or []),  # pyrefly: ignore [missing-attribute]
            "trials": runs,
            "passes": passes,
            "per_trial_rate": rate,
            "upper_bound": wilson_interval(rate, runs)[1],
            "solved_all3": int(entry.get("solved_all3") or 0),  # pyrefly: ignore [missing-attribute]
        }
    return None


def learning_evidence(metrics: dict[str, Any], window: str, table: dict[str, Any]) -> dict[str, Any]:
    return {
        "n": metrics["n"],
        "accepted": metrics["accepted"],
        "lower_bound": metrics["interval"][0],
        "cost_per_accepted": metrics["cost_per_accepted"],
        "window": window,
        "policy_rev": table.get("policy_rev"),
    }
