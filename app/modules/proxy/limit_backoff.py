from __future__ import annotations

import logging
import random
import time

from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)

_SHORT_HOLD_SECONDS = 60
_PROBE_WAIT_SECONDS = 10
_ready: set[str] = set()
_probing: dict[str, float] = {}


def enabled() -> bool:
    return bool(get_settings().anthropic_limit_backoff_enabled)


def probe_at(account_id: str, reset_at: int) -> int:
    spread = float(get_settings().anthropic_limit_probe_jitter_seconds)
    return int(reset_at) + int(random.Random(f"{account_id}:{int(reset_at)}").uniform(0.0, spread))


def hold_until(
    account_id: str,
    cooldown: tuple[float, int | None, int] | None,
    now: int,
    headroom_at: int | None = None,
) -> int | None:
    if cooldown is None or float(cooldown[0]) < 100.0 or cooldown[1] is None:
        _settle(account_id, "cleared")
        return None
    reset_at, recorded_at = int(cooldown[1]), int(cooldown[2])
    if not enabled():
        until = min(reset_at, recorded_at + _SHORT_HOLD_SECONDS)
        return until if until > now else None
    recovered = headroom_at is not None and headroom_at > recorded_at
    due = now if recovered else probe_at(account_id, reset_at)
    if now < due:
        _settle(account_id, "limited")
        return due
    expires = _probing.get(account_id)
    if expires is not None and expires > now:
        return min(int(expires), now + _PROBE_WAIT_SECONDS)
    _ready.add(account_id)
    return None


def claim(account_id: str) -> bool:
    if account_id not in _ready or not enabled():
        return True
    now = time.time()
    expires = _probing.get(account_id)
    if expires is not None and expires > now:
        return False
    _probing[account_id] = now + float(get_settings().anthropic_limit_probe_timeout_seconds)
    logger.info("limit_probe_sent account_id=%s", account_id)
    return True


def _settle(account_id: str, outcome: str) -> None:
    if _probing.pop(account_id, None) is not None:
        logger.info("limit_probe_settled account_id=%s outcome=%s", account_id, outcome)
    _ready.discard(account_id)
