"""Short-lived inventory snapshots; an expired count is unknown, not zero."""

from time import monotonic

_COUNTS_TTL_SECONDS = 300
_counts: dict[str, tuple[int, float]] = {}


def record_count(account_id: str, available_count: int) -> None:
    _counts[account_id] = (available_count, monotonic())


def clear(account_id: str) -> None:
    _counts.pop(account_id, None)


def get_count(account_id: str) -> int | None:
    cached = _counts.get(account_id)
    if cached is None:
        return None
    count, recorded = cached
    if monotonic() - recorded >= _COUNTS_TTL_SECONDS:
        return None
    return count


def reset() -> None:
    _counts.clear()
