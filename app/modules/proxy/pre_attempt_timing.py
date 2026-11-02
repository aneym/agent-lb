from __future__ import annotations

import logging
import time

from fastapi import Request

from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)


class PreAttemptTimer:
    def __init__(self, request: Request) -> None:
        state = request.state
        now = time.monotonic()
        self._origin = getattr(state, "arrived_at", now)
        self._marks: list[tuple[str, float]] = []
        request_id_at = getattr(state, "request_id_at", None)
        if request_id_at is not None:
            self._marks.append(("outer_middleware", request_id_at))
        self._marks.append(("dependencies", now))

    def mark(self, name: str) -> None:
        self._marks.append((name, time.monotonic()))

    def log(self, surface: str) -> None:
        previous = self._origin
        phases = []
        for name, at in self._marks:
            phases.append(f"{name}_ms={(at - previous) * 1000:.0f}")
            previous = at
        logger.info(
            "Pre-attempt timing request_id=%s surface=%s total_ms=%.0f %s",
            get_request_id(),
            surface,
            (previous - self._origin) * 1000,
            " ".join(phases),
        )
