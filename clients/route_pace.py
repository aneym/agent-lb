from __future__ import annotations

import json
import sys
from typing import Any

from route_anthropic_models import http_get  # pyrefly: ignore [missing-import]
from route_seat import pool_remaining  # pyrefly: ignore [missing-import]
from route_versioned_rules import EXIT_DEGRADED, EXIT_OK, pool_exhausted  # pyrefly: ignore [missing-import]

TIERS = ("critical", "behind", "on_pace", "ahead", "rich")
WORDS = {"on_pace": "on pace", "rich": "running rich", "exhausted": "empty"}


def pace_words(state: str | None) -> str:
    return WORDS.get(state or "unknown", state or "unknown")


def shadow_pace(pool: dict[str, Any]) -> dict[str, Any]:
    facts = pool.get("pace") if isinstance(pool.get("pace"), dict) else {}
    tier = facts.get("tier") if facts.get("tier") in TIERS else None
    return {
        "shadow": True,
        "state": "exhausted" if pool_exhausted(pool) else tier or "unknown",
        "tier": tier,
        "rho": facts.get("rho"),
        "b_star": facts.get("b_star"),
        "stale": facts.get("stale"),
        "remaining": pool_remaining(pool),
    }


def command_pace(args) -> int:
    result = http_get("/api/pools/pace")
    if not result.ok or not isinstance(result.body, dict):
        print("route: canonical shadow pace unavailable", file=sys.stderr)
        return EXIT_DEGRADED
    if args.json:
        print(json.dumps(result.body, sort_keys=True))
        return EXIT_OK
    print("Shadow only: nothing is enforced")
    for name, pace in result.body["pools"].items():
        rho = pace.get("rho")
        print(
            f"{name:<10} {pace_words(pace['tier']):<13} b* {pace['b_star']:.2f} points/h  "
            f"rho {'-' if rho is None else f'{rho:.2f}'}  eligible {pace['eligible_accounts']}/{pace['accounts']}"
        )
    share = result.body.get("s_star")
    print(f"S* {'-' if share is None else f'{share:.2f}'}")
    return EXIT_OK
