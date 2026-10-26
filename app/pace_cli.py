from __future__ import annotations

import json
import os

from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, get_status_json


def run(args) -> None:
    base = args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL)
    try:
        result = get_status_json(base.rstrip("/") + "/api/pools/pace", args.timeout)
    except StatusObservationError as exc:
        print(json.dumps({"shadow": True, "error": str(exc), "pools": {}}))
        raise SystemExit(2) from exc
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("Shadow only")
        for name, pace in result["pools"].items():
            print(f"{name}: {pace['tier']}, b* {pace['b_star']:.3f} points/h")
