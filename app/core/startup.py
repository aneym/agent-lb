"""Startup state management."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from typing import AsyncIterator, Callable

logger = logging.getLogger(__name__)

_startup_complete: bool = False
_bridge_registration_complete: bool = False
_bridge_durable_schema_ready: bool = False
_bridge_registration_event: asyncio.Event | None = None


@dataclass(frozen=True, slots=True)
class StartupSummary:
    boot_id: str
    outcome: str
    total_seconds: float
    untracked_seconds: float
    phases: dict[str, float]


def _process_started_ns() -> int:
    raw = os.environ.get("AGENT_LB_PROCESS_STARTED_NS", "")
    try:
        return int(raw)
    except ValueError:
        return time.perf_counter_ns()


def _prometheus_metric(name: str):
    try:
        return getattr(import_module("app.core.metrics.prometheus"), name, None)
    except (ImportError, AttributeError):
        return None


def _observe_startup_phase(name: str, outcome: str, seconds: float) -> None:
    metric = _prometheus_metric("startup_phase_duration_seconds")
    if metric is not None:
        metric.labels(phase=name, outcome=outcome).observe(seconds)


def _observe_startup_total(outcome: str, seconds: float) -> None:
    histogram = _prometheus_metric("startup_duration_seconds")
    if histogram is not None:
        histogram.labels(outcome=outcome).observe(seconds)
    gauge = _prometheus_metric("startup_complete_seconds")
    if gauge is not None:
        gauge.set(seconds)


def _observe_startup_readiness(seconds: float) -> None:
    histogram = _prometheus_metric("startup_readiness_duration_seconds")
    if histogram is not None:
        histogram.observe(seconds)
    gauge = _prometheus_metric("startup_ready_seconds")
    if gauge is not None:
        gauge.set(seconds)


class StartupRecorder:
    def __init__(
        self,
        *,
        started_ns: int | None = None,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
        boot_id: str | None = None,
    ) -> None:
        self.boot_id = boot_id or uuid.uuid4().hex
        self._clock_ns = clock_ns
        self._started_ns = _process_started_ns() if started_ns is None else started_ns
        self._phases: dict[str, float] = {}

    @asynccontextmanager
    async def phase(self, name: str) -> AsyncIterator[None]:
        phase_started_ns = self._clock_ns()
        outcome = "ok"
        try:
            yield
        except BaseException:
            outcome = "failed"
            raise
        finally:
            seconds = (self._clock_ns() - phase_started_ns) / 1_000_000_000
            self._phases[name] = self._phases.get(name, 0.0) + seconds
            _observe_startup_phase(name, outcome, seconds)

    def complete(self, outcome: str) -> StartupSummary:
        total_seconds = (self._clock_ns() - self._started_ns) / 1_000_000_000
        untracked_seconds = max(0.0, total_seconds - sum(self._phases.values()))
        summary = StartupSummary(
            boot_id=self.boot_id,
            outcome=outcome,
            total_seconds=total_seconds,
            untracked_seconds=untracked_seconds,
            phases=dict(self._phases),
        )
        _observe_startup_total(outcome, total_seconds)
        logger.info(
            "agent_lb_startup_summary boot_id=%s outcome=%s total_seconds=%.6f untracked_seconds=%.6f phases=%s",
            summary.boot_id,
            summary.outcome,
            summary.total_seconds,
            summary.untracked_seconds,
            json.dumps(summary.phases, sort_keys=True, separators=(",", ":")),
        )
        return summary

    def ready(self) -> float:
        total_seconds = (self._clock_ns() - self._started_ns) / 1_000_000_000
        _observe_startup_readiness(total_seconds)
        logger.info(
            "agent_lb_startup_ready boot_id=%s total_seconds=%.6f",
            self.boot_id,
            total_seconds,
        )
        return total_seconds


def reset_bridge_registration() -> None:
    global _bridge_registration_complete, _bridge_durable_schema_ready, _bridge_registration_event
    _bridge_registration_complete = False
    _bridge_durable_schema_ready = False
    _bridge_registration_event = asyncio.Event()


def mark_bridge_durable_schema_ready() -> None:
    global _bridge_durable_schema_ready
    _bridge_durable_schema_ready = True


def mark_bridge_registration_complete() -> None:
    global _bridge_registration_complete
    _bridge_registration_complete = True
    if _bridge_registration_event is not None:
        _bridge_registration_event.set()


async def wait_for_bridge_registration(timeout_seconds: float) -> bool:
    if _bridge_registration_complete:
        return True
    if _bridge_registration_event is None:
        return False
    try:
        await asyncio.wait_for(_bridge_registration_event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False
    return True
