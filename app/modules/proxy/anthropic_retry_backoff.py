import random


def connect_backoff_seconds(attempt: int) -> float:
    return min(0.5 * (2**attempt), 4.0)


def overloaded_backoff_jitter() -> float:
    return random.uniform(0.0, 0.25)
