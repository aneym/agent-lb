from __future__ import annotations

import math
import time

SECONDS_PER_DAY = 60 * 60 * 24
RATE_LIMIT_RESET_MAX_HORIZON_SECONDS = 366 * SECONDS_PER_DAY
RATE_LIMIT_RESET_ROUNDING_TOLERANCE_SECONDS = 1.0


def plausible_rate_limit_reset_at(
    reset_at: int | float | None, *, now: float | None = None, allow_rounding_slack: bool = True
) -> float | None:
    if reset_at is None or isinstance(reset_at, bool):
        return None
    current = time.time() if now is None else now
    if isinstance(reset_at, float) and (not math.isfinite(reset_at)):
        return None
    horizon = RATE_LIMIT_RESET_MAX_HORIZON_SECONDS
    if allow_rounding_slack:
        horizon += RATE_LIMIT_RESET_ROUNDING_TOLERANCE_SECONDS
    if reset_at <= current or reset_at > current + horizon:
        return None
    return float(reset_at)
