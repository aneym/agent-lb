from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def dispatch_choice(
    table: dict[str, Any],
    class_name: str,
    rng: Any,
    pools: dict[str, Any],
    standard: dict[str, Any],
    explore: dict[str, Any],
    shares: list,
    draw: float,
) -> dict[str, Any]:
    reason = "standard"
    if not explore.get("enabled"):
        reason = "standard (explore disabled)"
    elif shares:
        reason = f"standard after draw {draw:.4f}"
    spread = table.get("spread") or {}
    arms = (spread.get("classes") or {}).get(class_name)
    if class_name not in (table.get("mission_critical") or []) and arms is not None:
        reason = "spread: no eligible pool"
        candidates = []
        for arm in arms:
            seat = arm["seat"]
            pool_id = arm["pool"]
            pool = pools.get(pool_id, {})
            if pool_id.startswith("anthropic") or pool.get("provider") == "anthropic":
                continue
            if (
                seat not in (table.get("seats") or {})
                and not (Path.home() / ".claude" / "agents" / f"{seat}.md").is_file()
            ):
                continue
            headroom = pool.get("headroomPercent")
            if (
                not isinstance(headroom, (int, float))
                or isinstance(headroom, bool)
                or not math.isfinite(headroom)
                or headroom < spread.get("min_headroom_percent", 15)
                or pool.get("status") in ("exhausted", "empty")
            ):
                continue
            weight = float(arm["strength"]) * headroom / 100
            if math.isfinite(weight) and weight > 0:
                candidates.append((arm, weight))
        if candidates:
            total = sum(weight for _, weight in candidates)
            target = rng.random() * total
            cumulative = 0.0
            for arm, weight in candidates:
                cumulative += weight
                if target < cumulative:
                    return {
                        "arm": "spread",
                        "arm_id": f"spread:{arm['seat']}",
                        "seat": arm["seat"],
                        "model_alias": arm["model"],
                        "effort": arm.get("effort"),
                        "pool": arm["pool"],
                        "propensity": weight / total,
                        "reason": f"spread: {arm['seat']} w={weight:.4f} of {len(candidates)} eligible",
                    }
    return {
        "arm": "default",
        "arm_id": "standard",
        "seat": standard["seat"],
        "model_alias": standard["model"],
        "effort": standard.get("effort"),
        "pool": None,
        "propensity": round(1.0 - sum(share for _, share in shares), 6),
        "reason": reason,
    }
