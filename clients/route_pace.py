from __future__ import annotations

import json
import sys
from importlib import import_module

_routes = import_module("route_anthropic_models")
_rules = import_module("route_versioned_rules")
http_get = _routes.http_get
EXIT_DEGRADED, EXIT_OK = _rules.EXIT_DEGRADED, _rules.EXIT_OK


def command_pace(args) -> int:
    result = http_get("/api/pools/pace")
    if not result.ok or not isinstance(result.body, dict):
        print("route: canonical shadow pace unavailable", file=sys.stderr)
        return EXIT_DEGRADED
    if args.json:
        print(json.dumps(result.body, sort_keys=True))
    else:
        print("Shadow only")
        for name, pace in result.body["pools"].items():
            print(f"{name}: {pace['tier']}, b* {pace['b_star']:.3f} points/h")
    return EXIT_OK
