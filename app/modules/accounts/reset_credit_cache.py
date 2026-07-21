"""In-memory reset-credit counts, repopulated by the immediate expiry sweep after boot."""

_counts: dict[str, int] = {}


def record_count(account_id: str, available_count: int) -> None:
    _counts[account_id] = available_count


def clear(account_id: str) -> None:
    _counts.pop(account_id, None)


def get_count(account_id: str) -> int | None:
    return _counts.get(account_id)


def reset() -> None:
    _counts.clear()
