from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field

from app.core.metrics import prometheus as metrics

logger = logging.getLogger(__name__)

_WARNING_RATE_LIMIT_SECONDS = 10.0


@dataclass(slots=True)
class EventLoopLagMonitor:
    """Samples asyncio scheduling drift once per second and exports it.

    A blocked event loop (sync I/O, CPU-bound work, GC pauses) shows up as
    positive lag between the requested wake time and the actual wake time.
    """

    warning_threshold_seconds: float = 0.5
    check_interval_seconds: float = 1.0
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _last_warning_monotonic: float = field(default=0.0, init=False)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            target = loop.time() + self.check_interval_seconds
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.check_interval_seconds)
            except TimeoutError:
                self._record_lag(max(0.0, loop.time() - target))
            else:
                break

    def _record_lag(self, lag: float) -> None:
        if metrics.event_loop_lag_seconds is not None:
            metrics.event_loop_lag_seconds.set(lag)
        if lag <= self.warning_threshold_seconds:
            return
        if metrics.event_loop_lag_events_total is not None:
            metrics.event_loop_lag_events_total.inc()
        now = time.monotonic()
        if now - self._last_warning_monotonic < _WARNING_RATE_LIMIT_SECONDS:
            return
        self._last_warning_monotonic = now
        logger.warning("event_loop_lag lag=%.3fs", lag)


def build_event_loop_lag_monitor(*, warning_threshold_seconds: float) -> EventLoopLagMonitor:
    return EventLoopLagMonitor(warning_threshold_seconds=warning_threshold_seconds)
