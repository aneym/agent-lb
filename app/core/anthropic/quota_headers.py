from __future__ import annotations

import math
import time
from collections.abc import Mapping
from datetime import datetime

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


def parse_reset_epoch(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip()
    try:
        epoch = float(normalized)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            return int(parsed.timestamp()) if parsed.tzinfo is not None else None
        except (ValueError, OverflowError, OSError):
            return None
    if not math.isfinite(epoch) or epoch <= 0:
        return None
    return int(epoch / 1000 if epoch > 1e12 else epoch)


def account_quota_windows(headers: Mapping[str, str]) -> dict[str, tuple[float, int | None, int]]:
    normalized = capture_unified_headers(headers)
    windows = {}
    for name, window, minutes in (("5h", "primary", 300), ("7d", "secondary", 10080)):
        prefix = f"{PREFIX}{name}-"
        utilization = parse_unified_utilization(normalized.get(f"{prefix}utilization"))
        if utilization is not None and utilization > 1:
            utilization = None
        rejected = normalized.get(f"{prefix}status", "").strip().lower() == "rejected"
        if utilization is None and not rejected:
            continue
        percent = max(100.0 if rejected else 0.0, (utilization or 0.0) * 100)
        windows[window] = (percent, parse_reset_epoch(normalized.get(f"{prefix}reset")), minutes)
    return windows


def exhausted_account_reset(headers: Mapping[str, str]) -> int | None:
    resets = [
        reset
        for percent, reset, _ in account_quota_windows(headers).values()
        if percent >= 100 and reset is not None and reset > time.time()
    ]
    return max(resets) if resets else None
