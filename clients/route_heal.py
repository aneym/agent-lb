from __future__ import annotations

import math
import random
from typing import Any


def standard_choice(standard: dict, reason: str) -> dict:
    return {
        "arm": "default",
        "arm_id": "standard",
        "seat": standard["seat"],
        "model_alias": standard["model"],
        "effort": standard.get("effort"),
        "pool": None,
        "propensity": 1.0,
        "reason": reason,
    }


def seat_block(api: Any, ctx: Any, arm: dict, excluded: set[str]) -> str | None:
    if arm.get("seat") in excluded:
        return f"excluded seat {arm['seat']}"
    spec = (ctx.table.get("seats") or {}).get(arm.get("seat")) or {}
    if any(row.get("paused") or row.get("disabled") or row.get("enabled") is False for row in (arm, spec)):
        return "paused or disabled"
    gate = arm.get("gate")
    if isinstance(gate, dict) and gate.get("open") is False:
        return "gate closed"
    if arm.get("author_vendor") and arm["author_vendor"] != ctx.author_vendor:
        return "author vendor incompatible"
    if ctx.class_name == "verify":
        reviewer = api.dispatch_standard(ctx.table, "verify", ctx.author_vendor)
        if arm.get("seat") != reviewer["seat"]:
            return "reviewer must match the cross-vendor author standard"
    return api.hands_refusal(ctx.table, ctx.class_name, arm.get("seat"), arm.get("model"))


def fallback(
    api: Any, ctx: Any, standard: dict, excluded: set[str], reason: str, pools: dict, today: list
) -> dict | None:
    table = ctx.table
    class_name = ctx.class_name
    spread = ((table.get("spread") or {}).get("classes") or {}).get(class_name) or []
    explore = ((table.get("explore") or {}).get("arms") or {}).get(class_name) or []
    for kind, arms in (("spread", spread), ("explore", explore)):
        for arm in arms:
            if seat_block(api, ctx, arm, excluded) or (
                kind == "explore" and (api.arm_block(ctx, arm) or api.arm_cap_block(ctx, arm, today))
            ):
                continue
            if kind == "spread":
                pool = pools.get(arm.get("pool"), {})
                headroom = pool.get("headroomPercent")
                if (
                    not isinstance(headroom, (int, float))
                    or isinstance(headroom, bool)
                    or not math.isfinite(headroom)
                    or headroom < (table.get("spread") or {}).get("min_headroom_percent", 15)
                    or pool.get("status") in ("exhausted", "empty")
                ):
                    continue
            return {
                **standard_choice(arm, reason),
                "arm": kind,
                "arm_id": arm.get("id") or f"spread:{arm['seat']}",
                "pool": arm.get("pool"),
            }
    variants = [standard, api.dispatch_standard(table, class_name, ctx.author_vendor)]
    variants.extend((table["standards"][class_name].get("by_author_vendor") or {}).values())
    override = (table.get("quota_swap") or {}).get(class_name)
    if isinstance(override, dict):
        variants.append({**standard, **override})
    for variant in variants:
        if not seat_block(api, ctx, variant, excluded):
            return standard_choice(variant, reason)
    vendor = api.alias_vendor(table, standard.get("model"))
    seat, model = (
        ("gpt-implementer", "sol-latest") if vendor == "anthropic" else ("sonnet-implementer", "sonnet-latest")
    )
    if class_name not in ("implement", "mechanical"):
        seat = "astra-consult" if vendor == "anthropic" else "opus-seat"
        model = "astra-latest" if vendor == "anthropic" else "opus-latest"
    variant = {**standard, "seat": seat, "model": model}
    if seat in table.get("seats", {}) and not seat_block(api, ctx, variant, excluded):
        return standard_choice(variant, reason)
    return None


def select(
    api: Any,
    args: Any,
    table: dict,
    standard: dict,
    ctx: Any,
    prior: list,
    history: list,
    mode_state: dict,
    pools_doc: dict,
    job_id: str,
) -> tuple:
    class_name = args.task_class
    explore = table.get("explore") or {}
    excluded = set(args.exclude)
    explicit = any(value is not None for value in (args.seat, args.model, args.effort))
    eligible = []
    sticky = False
    notes = []
    today_key = api.decision_day(api.now_iso())
    today = [row for row in history if row.get("arm") == "explore" and api.decision_day(row.get("ts")) == today_key]
    choice = None
    if explicit:
        seat = args.seat or standard["seat"]
        spec = api.seat_defaults(table, class_name, seat)
        choice = {
            "arm": "explicit",
            "arm_id": "explicit",
            "seat": seat,
            "model_alias": args.model or (standard["model"] if seat == standard["seat"] else spec.get("model")),
            "effort": args.effort or (standard.get("effort") if seat == standard["seat"] else spec.get("effort")),
            "pool": None,
            "propensity": 1.0,
            "reason": "explicit flags",
        }
    elif prior:
        first = prior[0]
        arms = {arm.get("id"): arm for arm in (explore.get("arms") or {}).get(class_name) or []}
        spread = ((table.get("spread") or {}).get("classes") or {}).get(class_name) or []
        arm = first
        blocked = None
        if first.get("arm") == "explore":
            arm = arms.get(first.get("arm_id")) or {"paused": True}
            blocked = api.arm_block(ctx, arm)
        elif first.get("arm") == "spread":
            arm = next((row for row in spread if row.get("seat") == first.get("seat")), {"paused": True})
        blocked = blocked or seat_block(api, ctx, {**first, **arm}, excluded)
        if blocked:
            notes.append(f"dropped sticky arm {first.get('arm_id')}: {blocked}")
        else:
            sticky = True
            choice = {
                "arm": first.get("arm") or "default",
                "arm_id": first.get("arm_id") or "standard",
                "seat": first.get("seat"),
                "model_alias": first.get("model_alias"),
                "effort": first.get("effort"),
                "pool": first.get("pool"),
                "propensity": first.get("propensity", 1.0),
                "reason": f"sticky: job {job_id} keeps {first.get('decision_id')}",
            }
            eligible = list(first.get("eligible_arms") or [])
    if choice is None:
        today_key = api.decision_day(api.now_iso())
        today = [row for row in history if row.get("arm") == "explore" and api.decision_day(row.get("ts")) == today_key]
        candidates = []
        for arm in (explore.get("arms") or {}).get(class_name) or []:
            if not isinstance(arm, dict) or seat_block(api, ctx, arm, excluded):
                continue
            if mode_state["mode"] == "codex-swap" and class_name in (table.get("quota_swap") or {}):
                continue
            if api.arm_block(ctx, arm) or api.arm_cap_block(ctx, arm, today):
                continue
            candidates.append(arm)
        total = sum(float(arm.get("share") or 0) for arm in candidates)
        cap = float(explore.get("max_share_per_class") or 0)
        scale = cap / total if total > cap and total > 0 else 1.0
        shares = [(arm, float(arm.get("share") or 0) * scale) for arm in candidates]
        eligible = [arm["id"] for arm in candidates]
        rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
        draw = rng.random()
        cumulative = 0.0
        for arm, share in shares:
            cumulative += share
            if draw < cumulative:
                choice = {
                    **standard_choice(arm, f"explore draw {draw:.4f} < {cumulative:.4f}"),
                    "arm": "explore",
                    "arm_id": arm["id"],
                    "pool": arm.get("pool"),
                    "propensity": round(share, 6),
                }
                break
        if choice is None:
            spread = table.get("spread") or {}
            classes = spread.get("classes") or {}
            filtered = {
                **table,
                "spread": {
                    **spread,
                    "classes": {
                        **classes,
                        class_name: [
                            arm for arm in classes.get(class_name) or [] if not seat_block(api, ctx, arm, excluded)
                        ],
                    },
                },
            }
            choice = api.dispatch_choice(
                filtered, class_name, rng, api.pools_by_id(pools_doc), standard, explore, shares, draw
            )
    if not explicit and not sticky:
        leases = (lambda: pools_doc.get("reservations")) if args.pools_file else api.pick_leases
        constrained, skipped = api.constraint_choice(
            table, class_name, api.pools_by_id(pools_doc), args.tag, mode_state["mode"], api.ledger_path(), leases
        )
        if constrained and constrained["seat"] not in excluded:
            choice = constrained
        elif skipped:
            notes.append(f"grok skipped: {skipped}")
    refused = api.hands_refusal(table, class_name, choice["seat"], choice["model_alias"])
    if refused:
        notes.append(f"replaced {choice['seat']}: {refused}")
        choice = standard_choice(standard, "standard")
        sticky = False
    blocked = seat_block(api, ctx, {**choice, "model": choice["model_alias"]}, excluded)
    if blocked:
        notes.append(blocked)
        choice = fallback(
            api, ctx, standard, excluded, "reselected after unavailable seat", api.pools_by_id(pools_doc), today
        )
        sticky = False
    if choice is not None:
        choice["reason"] = "; ".join([standard.get("fallback_reason", choice["reason"]), *notes])
    return choice, eligible, sticky


def reserve_candidates(request: Any, payload: dict) -> tuple[int, dict]:
    candidates = list(payload["candidates"])
    code, result = 75, {"status": "wait", "reason": "all capacity keys exhausted"}
    while candidates:
        candidate = candidates[0]
        code, result = request("POST", "", {**payload, "candidates": [candidate]})
        if code == 0 and result.get("status") == "reserved":
            return code, result
        if code == 0 and result.get("status") != "wait":
            return code, result
        key = candidate["capacity_key"]
        candidates = [row for row in candidates if row["capacity_key"] != key]
    return (75 if result.get("status") == "wait" else code), result
