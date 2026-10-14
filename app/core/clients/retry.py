from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetryOptions:


    attempts: int = 3
    start_timeout: float = 0.5
    max_timeout: float = 2.0
    factor: float = 2.0
    retryable_statuses: frozenset[int] = field(default_factory=lambda: frozenset({408, 429, 500, 502, 503, 504}))


def calculate_backoff_delay(
    attempt: int,
    start_timeout: float,
    max_timeout: float,
    factor: float,
) -> float:

    delay = min(
        start_timeout * (factor**attempt),
        max_timeout,
    )

    delay *= 0.5 + random.random() * 0.5
    return delay
