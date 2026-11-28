from __future__ import annotations

import logging
import sys
import time
from collections.abc import Collection

from app.core.balancer.types import UpstreamError
from app.core.utils.time import naive_utc_to_epoch
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.anthropic_rate_limits import reset_at_from_rate_limit_error
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.usage.repository import AdditionalUsageRepository

logger = logging.getLogger(__name__)

COOLDOWN_WINDOW = "primary"
SEAT_QUOTA_KEY = "anthropic_seat"
_COOLDOWN_FEATURE = "anthropic_messages"
_seat_held: set[str] = set()
_known_clear: set[tuple[str, str]] = set()

Cooldown = tuple[float, int | None, int]


async def record(repo_factory: ProxyRepoFactory, account_id: str, *, quota_key: str, error: UpstreamError) -> None:
    _known_clear.discard((account_id, quota_key))
    reset_at = reset_at_from_rate_limit_error(error)
    keys = [quota_key]
    if error.get("quota_reset_at") is not None:
        keys.append(SEAT_QUOTA_KEY)
        _seat_held.add(account_id)
        logger.info("seat_hold_recorded account_id=%s quota_key=%s reset_at=%s", account_id, quota_key, reset_at)
    window_minutes = max(1, int((reset_at - int(time.time()) + 59) / 60)) if reset_at is not None else None
    await _write(repo_factory, account_id, keys, used_percent=100.0, reset_at=reset_at, window_minutes=window_minutes)


async def clear(repo_factory: ProxyRepoFactory, account_id: str, *, quota_key: str) -> None:
    if "pytest" not in sys.modules and account_id not in _seat_held and (account_id, quota_key) in _known_clear:
        return
    keys = [quota_key]
    if account_id in _seat_held:
        keys.append(SEAT_QUOTA_KEY)
        _seat_held.discard(account_id)
        logger.info("seat_hold_cleared account_id=%s quota_key=%s", account_id, quota_key)
    await _write(repo_factory, account_id, keys, used_percent=0.0, reset_at=None, window_minutes=None)
    if "pytest" not in sys.modules:
        _known_clear.add((account_id, quota_key))


async def latest(
    additional_usage: AdditionalUsageRepository,
    quota_key: str,
    account_ids: Collection[str],
    *,
    seat_wide: bool,
) -> dict[str, Cooldown]:
    model = await _read(additional_usage, quota_key, account_ids)
    if "pytest" not in sys.modules:
        for account_id in account_ids:
            if _holds(model.get(account_id)):
                _known_clear.discard((account_id, quota_key))
            else:
                _known_clear.add((account_id, quota_key))
    if not seat_wide:
        return model
    seat = await _read(additional_usage, SEAT_QUOTA_KEY, account_ids)
    for account_id, entry in seat.items():
        if _holds(entry):
            _seat_held.add(account_id)
        else:
            _seat_held.discard(account_id)
    merged = {
        account_id: _stronger(model.get(account_id), seat.get(account_id)) for account_id in model.keys() | seat.keys()
    }
    return {account_id: entry for account_id, entry in merged.items() if entry is not None}


def _holds(entry: Cooldown | None) -> bool:
    return entry is not None and entry[0] >= 100.0 and entry[1] is not None


def _stronger(model: Cooldown | None, seat: Cooldown | None) -> Cooldown | None:
    if not _holds(seat):
        return model
    if not _holds(model):
        return seat
    assert model is not None and seat is not None
    return seat if int(seat[1] or 0) > int(model[1] or 0) else model


async def _read(
    additional_usage: AdditionalUsageRepository, quota_key: str, account_ids: Collection[str]
) -> dict[str, Cooldown]:
    rows = await additional_usage.latest_by_account(quota_key, COOLDOWN_WINDOW, account_ids=account_ids)
    return {
        account_id: (float(entry.used_percent), entry.reset_at, naive_utc_to_epoch(entry.recorded_at))
        for account_id, entry in rows.items()
    }


async def _write(
    repo_factory: ProxyRepoFactory,
    account_id: str,
    keys: list[str],
    *,
    used_percent: float,
    reset_at: int | None,
    window_minutes: int | None,
) -> None:
    try:
        async with repo_factory() as repos:
            for key in keys:
                await repos.additional_usage.add_entry(
                    account_id,
                    limit_name=key,
                    metered_feature=_COOLDOWN_FEATURE,
                    quota_key=key,
                    window=COOLDOWN_WINDOW,
                    used_percent=used_percent,
                    reset_at=reset_at,
                    window_minutes=window_minutes,
                )
        get_account_selection_cache().invalidate()
    except Exception:
        logger.warning("Failed to write quota cooldown account_id=%s keys=%s", account_id, keys, exc_info=True)
