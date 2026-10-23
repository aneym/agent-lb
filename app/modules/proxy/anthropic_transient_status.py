TRANSIENT_UPSTREAM_STATUSES = frozenset({500, 502, 503, 504, 529})


def exhausted_error_code(last_status: int | None, fallback_code: str) -> str:
    if last_status == 529:
        return "overloaded_error"
    if last_status in TRANSIENT_UPSTREAM_STATUSES:
        return "api_error"
    return fallback_code
