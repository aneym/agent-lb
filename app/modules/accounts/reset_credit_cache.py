from time import monotonic

_COUNTS_TTL_SECONDS = 300
_counts: dict[str, tuple[int, list[str] | None, float]] = {}


def record_count(account_id: str, available_count: int, clears: list[str] | None = None) -> None:
    if clears is None and available_count > 0:
        cached = _fresh(account_id)
        clears = None if cached is None else cached[1]
    _counts[account_id] = (available_count, [] if available_count == 0 else clears, monotonic())


def clear(account_id: str) -> None:
    _counts.pop(account_id, None)


def _fresh(account_id: str) -> tuple[int, list[str] | None, float] | None:
    cached = _counts.get(account_id)
    if cached is None or monotonic() - cached[2] >= _COUNTS_TTL_SECONDS:
        return None
    return cached


def get_count(account_id: str) -> int | None:
    cached = _fresh(account_id)
    return None if cached is None else cached[0]


def get_clears(account_id: str) -> list[str] | None:
    cached = _fresh(account_id)
    return None if cached is None else cached[1]


def reset() -> None:
    _counts.clear()
