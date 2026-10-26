from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping
from datetime import datetime
from typing import TypeVar

from app.modules.pools.pace_curve import same_window, timestamp

Ident = TypeVar("Ident", bound=Hashable)

HOUR_SECONDS = 3600
OUTPUT_WEIGHT = 5.0
CACHE_CREATION_WEIGHT = 1.25
CACHE_READ_WEIGHT = 0.1


def hour_index(value: datetime) -> int:
    stamp = timestamp(value)
    assert stamp is not None
    return int(stamp.timestamp() // HOUR_SECONDS)


def token_equivalents(input_tokens: int, output_tokens: int, cache_creation: int, cache_read: int) -> float:
    return (
        input_tokens
        + OUTPUT_WEIGHT * output_tokens
        + CACHE_CREATION_WEIGHT * cache_creation
        + CACHE_READ_WEIGHT * cache_read
    )


def weekly_rises(samples: Iterable[tuple[datetime, float, datetime | None]], factor: float) -> dict[int, float]:
    ordered = sorted(samples, key=lambda sample: timestamp(sample[0]) or datetime.min)
    rises: dict[int, float] = defaultdict(float)
    top = ordered[0][1] if ordered else 0.0
    for (_, _, reset_before), (at, after, reset_after) in zip(ordered, ordered[1:]):
        if not same_window(reset_before, reset_after):
            top = after
        elif after > top:
            rises[hour_index(at)] += (after - top) * factor
            top = after
    return dict(rises)


def apportion(
    rises: Mapping[tuple[str, int], float],
    loads: Mapping[tuple[str, int], Mapping[Ident, float]],
) -> tuple[dict[tuple[str, int], dict[Ident, float]], float]:
    shares: dict[tuple[str, int], dict[Ident, float]] = {}
    unexplained = 0.0
    for (account, hour), rise in rises.items():
        basis = loads.get((account, hour)) or {}
        if sum(basis.values()) <= 0:
            basis = loads.get((account, hour - 1)) or {}
        total = sum(basis.values())
        if total <= 0:
            unexplained += rise
            continue
        shares[(account, hour)] = {ident: rise * weight / total for ident, weight in basis.items() if weight > 0}
    return shares, unexplained
