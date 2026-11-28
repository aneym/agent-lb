from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable

from app.modules.proxy.anthropic_errors import AnthropicProxyError

logger = logging.getLogger(__name__)

_POOL_WAIT_MIN_POLL_SECONDS = 5.0
_POOL_WAIT_MAX_POLL_SECONDS = 300.0
_POOL_WAIT_MAX_JITTER_SECONDS = 5.0


def _default_pool_wait_jitter() -> float:
    return random.uniform(0.0, _POOL_WAIT_MAX_JITTER_SECONDS)


POOL_WAIT_CLOCK: Callable[[], float] = time.time
POOL_WAIT_SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep
POOL_WAIT_JITTER: Callable[[], float] = _default_pool_wait_jitter


def pool_wait_should_hold(
    exc: AnthropicProxyError,
    *,
    wait_enabled: bool,
    deadline: float | None,
) -> bool:
    if not wait_enabled or deadline is None:
        return False
    if exc.retry_at is None:
        return False
    return POOL_WAIT_CLOCK() < deadline


def pool_wait_delay_seconds(retry_at: int | None, *, now: float, remaining: float) -> float:
    base = float(retry_at) - now if retry_at is not None else _POOL_WAIT_MIN_POLL_SECONDS
    base = min(max(base, _POOL_WAIT_MIN_POLL_SECONDS), _POOL_WAIT_MAX_POLL_SECONDS)
    return max(0.0, min(base + POOL_WAIT_JITTER(), remaining))


async def hold_for_pool_reset(error: AnthropicProxyError, *, deadline: float | None) -> None:
    if deadline is None:
        raise error
    now = POOL_WAIT_CLOCK()
    remaining = deadline - now
    if remaining <= 0:
        raise error
    delay = pool_wait_delay_seconds(error.retry_at, now=now, remaining=remaining)
    logger.info(
        "Anthropic pool exhausted; holding stream open for reset retry_at=%s delay_seconds=%.1f",
        error.retry_at,
        delay,
    )
    if delay > 0:
        await POOL_WAIT_SLEEP(delay)
