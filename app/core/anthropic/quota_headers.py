from __future__ import annotations

import math
from collections.abc import Mapping

PREFIX = "anthropic-ratelimit-unified-"


def capture_unified_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items() if name.lower().startswith(PREFIX)}


def parse_unified_utilization(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None
