from __future__ import annotations

CAPACITY_COOLDOWN_SECONDS = 15.0
OVERLOAD_CODES = frozenset({"server_is_overloaded", "slow_down"})
CAPACITY_MESSAGE_CODES = frozenset(
    {
        "server_error",
        "rate_limit_exceeded",
        "turn_rate_limit_exceeded",
        "model_capacity_exceeded",
        "model_overloaded",
        "capacity_exceeded",
    }
)
_CAPACITY_MARKERS = ("at capacity", "currently overloaded")


def is_capacity_message(message: object) -> bool:
    if not isinstance(message, str):
        return False
    text = " ".join(message.lower().split())
    return any(marker in text for marker in _CAPACITY_MARKERS)


def is_capacity_error(code: object, message: object) -> bool:
    if code in OVERLOAD_CODES:
        return True
    return code in CAPACITY_MESSAGE_CODES and is_capacity_message(message)
