from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HANDS_CLASSES = frozenset({"implement", "mechanical"})
HANDS_SEATS = frozenset({"grok-implementer"})
HANDS_FAMILY = re.compile(r"^(?:cursor-)?(?:grok|composer)(?:-|$)")
EFFORT = re.compile(r"-(?:low|medium|high|xhigh|max)$")
LIMIT_TEXT = re.compile(r"\b429\b|usage[ _-]?limit|rate[ _-]?limit|quota[ _-]?exceeded", re.IGNORECASE)
CODEX_MODEL = re.compile(r"^(?:gpt-|sol)", re.IGNORECASE)
LEDGER_TAIL_BYTES = 4_000_000
BANDS = {0: "exhausted", 1: "critical", 2: "low"}


def hands_family(table: dict[str, Any], model: str | None) -> bool:
    if not model:
        return False
    aliases = table.get("aliases") if isinstance(table.get("aliases"), dict) else {}
    spec = aliases.get(model) or aliases.get(EFFORT.sub("", model))
    family = spec.get("family") if isinstance(spec, dict) else None
    return bool(HANDS_FAMILY.match(str(family or model).lower()))


def hands_refusal(table: dict[str, Any], class_name: str, seat: str | None, *models: str | None) -> str | None:
    if class_name in HANDS_CLASSES:
        return None
    if seat in HANDS_SEATS or any(hands_family(table, model) for model in models):
        return f"Grok and Composer are hands only: class {class_name} is not implement or mechanical"
    return None


def hands_seat(table: dict[str, Any], class_name: str, prefer: str | None, model: str | None) -> str | None:
    if prefer == "cursor-seat" and class_name in HANDS_CLASSES and hands_family(table, model):
        return next(iter(HANDS_SEATS))
    return prefer


def seat_defaults(table: dict[str, Any], class_name: str, seat: str) -> dict[str, Any]:
    spec = dict((table.get("seats") or {}).get(seat) or {})
    if seat in HANDS_SEATS:
        arm = next(
            (
                row
                for row in ((table.get("explore") or {}).get("arms") or {}).get(class_name) or []
                if isinstance(row, dict) and row.get("seat") == seat
            ),
            {},
        )
        spec.setdefault("model", arm.get("model"))
        spec.setdefault("effort", arm.get("effort"))
    return spec


def rung_block(table: dict[str, Any], class_name: str, entry: dict[str, Any], pools: dict[str, Any]) -> str | None:
    refused = hands_refusal(table, class_name, entry.get("seat"), entry.get("model"))
    if refused or entry.get("seat") not in HANDS_SEATS:
        return refused
    policy = (table.get("policy") or {}).get("grok") or {}
    return grok_gate(pools.get(policy.get("pool", "grok")), float(policy.get("max_weekly_used_percent", 80)))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def grok_gate(pool: dict[str, Any] | None, limit: float) -> str | None:
    if not isinstance(pool, dict):
        return "grok pool missing"
    eligible = _number(pool.get("eligibleAccounts"))
    if eligible == 0 or pool.get("status") == "exhausted":
        return "grok pool exhausted"
    if pool.get("status") == "stale" or str(pool.get("source") or "").endswith("_stale"):
        return "grok pool reading stale"
    windows = pool.get("windows") if isinstance(pool.get("windows"), list) else []
    used = _number(pool.get("percentUsed"))
    if used is None and windows and isinstance(windows[0], dict):
        used = _number(windows[0].get("usedPercent"))
    if used is None and _number(pool.get("headroomPercent")) is not None:
        used = 100.0 - float(pool["headroomPercent"])
    if used is None:
        return "grok pool usage unknown"
    if used >= limit:
        return f"grok pool {used:.1f}% used (cap {limit:g}%)"
    return None


def codex_limit_error(ledger: Path, now: datetime, minutes: float) -> bool:
    try:
        with ledger.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - LEDGER_TAIL_BYTES))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return False
    since = now - timedelta(minutes=minutes)
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("ts")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            continue
        if stamp < since:
            return False
        model = str(row.get("worker_model") or row.get("model") or row.get("dispatch_model") or "")
        text = f"{row.get('infra_reason') or ''} {row.get('error') or ''}"
        if row.get("event") == "closeout" and CODEX_MODEL.match(model) and LIMIT_TEXT.search(text):
            return True
    return False


def codex_reservations_full(
    table: dict[str, Any], pool: dict[str, Any], leases: dict[str, Any] | None, now_iso: str
) -> bool:
    if not isinstance(leases, dict) or not isinstance(leases.get("live"), list):
        return False
    concurrency = (table.get("policy") or {}).get("concurrency") or {}
    eligible = _number(pool.get("eligibleAccounts"))
    capacity = max(0.0, eligible if eligible is not None else 1.0) * (
        (concurrency.get("per_account") or {}).get("openai", concurrency.get("default", 2))
    )
    live = sum(
        1
        for row in leases["live"]
        if isinstance(row, dict) and row.get("capacity_key") == "openai" and str(row.get("expires_at") or "") > now_iso
    )
    return live >= capacity


def grok_faster(table: dict[str, Any], class_name: str) -> bool:
    evidence = ((table.get("standards") or {}).get(class_name) or {}).get("evidence")
    aa = evidence.get("aa") if isinstance(evidence, dict) else None
    if not isinstance(aa, dict) or not isinstance(aa.get("fields"), list):
        return False
    try:
        speed, coding = aa["fields"].index("output_tps"), aa["fields"].index(aa["coding_field"])
        grok = next(row for key, row in aa.items() if key.startswith("grok-") and isinstance(row, list))
        sol = next(row for key, row in aa.items() if key.startswith("gpt-") and isinstance(row, list))
        return grok[speed] > sol[speed] and grok[coding] >= sol[coding]
    except (KeyError, StopIteration, IndexError, TypeError, ValueError):
        return False


def constraint_choice(
    table: dict[str, Any],
    class_name: str,
    pools: dict[str, dict[str, Any]],
    tags: list[str],
    mode: str,
    ledger: Path,
    leases: Callable[[], dict[str, Any] | None],
) -> tuple[dict[str, Any] | None, str | None]:
    if class_name not in HANDS_CLASSES:
        return None, None
    now = datetime.now(timezone.utc)
    policy = (table.get("policy") or {}).get("grok") or {}
    arm = next(
        (
            row
            for row in ((table.get("explore") or {}).get("arms") or {}).get(class_name) or []
            if isinstance(row, dict) and row.get("seat") in HANDS_SEATS
        ),
        None,
    )
    excluded = {tag.lower().replace("_", "-") for tag in tags} & set(policy.get("never_tags") or [])
    if arm is None or excluded:
        return None, f"brief tagged {', '.join(sorted(excluded))}" if excluded else None
    codex = pools.get("openai-codex") or {}
    eligible = _number(codex.get("eligibleAccounts"))
    band = BANDS.get(int(eligible)) if eligible is not None and eligible <= 2 else codex.get("status")
    if band in BANDS.values():
        trigger = f"openai-codex is {band}"
    elif codex_limit_error(ledger, now, float(policy.get("codex_error_minutes", 30))):
        trigger = "Codex 429 or usage-limit error in the last 30 minutes"
    elif codex_reservations_full(table, codex, leases(), now.strftime("%Y-%m-%dT%H:%M:%SZ")):
        trigger = "Codex reservation cap reached"
    elif mode == "codex-swap" and (class_name == "implement" or grok_faster(table, class_name)):
        trigger = "codex-swap sends non-critical volume to Grok first"
    else:
        return None, None
    blocked = grok_gate(pools.get(policy.get("pool", "grok")), float(policy.get("max_weekly_used_percent", 80)))
    if blocked:
        return None, f"{trigger}, but {blocked}"
    return {
        "arm": "constraint",
        "arm_id": arm["id"],
        "seat": arm["seat"],
        "model_alias": arm["model"],
        "effort": arm.get("effort"),
        "pool": arm.get("pool"),
        "propensity": 1.0,
        "reason": f"constraint: {trigger}",
    }, None
