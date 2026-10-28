from __future__ import annotations

import math
from collections.abc import Mapping

EXCHANGE_SETTLE_S = 75
EXIT_MARGIN_S = 15
DRAIN_ADMIT_GRACE_S = 15


def minimum_exit_timeout(env: Mapping[str, str]) -> int:
    graceful = int(env.get("UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN", "75"))
    drain = float(env.get("AGENT_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS", "30"))
    return math.ceil(graceful + drain + EXCHANGE_SETTLE_S + EXIT_MARGIN_S)
