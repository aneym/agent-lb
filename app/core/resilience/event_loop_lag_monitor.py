from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field

from app.core.forensics import dump_all_thread_stacks
from app.core.metrics import prometheus as metrics

logger = logging.getLogger(__name__)

_WARNING_RATE_LIMIT_SECONDS = 10.0
_STALL_DUMP_RATE_LIMIT_SECONDS = 60.0


@dataclass(slots=True)
class EventLoopLagMonitor:
    """Samples asyncio scheduling drift once per second and exports it.

    A blocked event loop (sync I/O, CPU-bound work, GC pauses) shows up as
    positive lag between the requested wake time and the actual wake time.

    A companion watchdog *thread* observes the same heartbeat from outside the
    loop: the in-loop sampler can only report a stall after it ends, so the
    watchdog captures an all-threads stack dump while the stall is still in
    progress (the 2026-07-16 stalls left no forensic evidence because the
    SIGUSR2 dump path was manual-only).
    """

    warning_threshold_seconds: float = 0.5
    check_interval_seconds: float = 1.0
    stall_dump_threshold_seconds: float = 5.0
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _last_warning_monotonic: float = field(default=0.0, init=False)
    _heartbeat_monotonic: float = field(default=0.0, init=False)
    _watchdog_stop: threading.Event = field(default_factory=threading.Event, init=False)
    _watchdog_thread: threading.Thread | None = field(default=None, init=False)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._heartbeat_monotonic = time.monotonic()
        self._task = asyncio.create_task(self._run_loop())
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="event-loop-stall-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    async def stop(self) -> None:
        self._watchdog_stop.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=2.0)
            self._watchdog_thread = None
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
            self._heartbeat_monotonic = time.monotonic()
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

    def _watchdog_loop(self) -> None:
        last_dump_monotonic = 0.0
        while not self._watchdog_stop.wait(self.check_interval_seconds):
            now = time.monotonic()
            stalled_seconds = now - self._heartbeat_monotonic
            if stalled_seconds < self.stall_dump_threshold_seconds:
                continue
            if now - last_dump_monotonic < _STALL_DUMP_RATE_LIMIT_SECONDS:
                continue
            last_dump_monotonic = now
            dump_all_thread_stacks(
                header=f"event_loop_stall watchdog dump stalled>={stalled_seconds:.1f}s",
            )
            logger.warning("event_loop_stall_stack_dump stalled_seconds=%.1f", stalled_seconds)


def build_event_loop_lag_monitor(*, warning_threshold_seconds: float) -> EventLoopLagMonitor:
    return EventLoopLagMonitor(warning_threshold_seconds=warning_threshold_seconds)
