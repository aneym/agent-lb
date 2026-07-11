from __future__ import annotations

import logging

import pytest

from app.core import startup


class _Clock:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


@pytest.mark.asyncio
async def test_startup_recorder_uses_monotonic_phase_durations(monkeypatch, caplog) -> None:
    observed: list[tuple[str, str, float]] = []
    monkeypatch.setattr(
        startup, "_observe_startup_phase", lambda name, outcome, seconds: observed.append((name, outcome, seconds))
    )
    monkeypatch.setattr(
        startup, "_observe_startup_total", lambda outcome, seconds: observed.append(("total", outcome, seconds))
    )
    recorder = startup.StartupRecorder(
        started_ns=1_000_000_000, clock_ns=_Clock(1_100_000_000, 1_350_000_000, 1_500_000_000)
    )

    async with recorder.phase("database"):
        pass

    caplog.set_level(logging.INFO, logger="app.core.startup")
    summary = recorder.complete("ok")

    assert summary.total_seconds == pytest.approx(0.5)
    assert summary.phases == {"database": pytest.approx(0.25)}
    assert observed == [("database", "ok", pytest.approx(0.25)), ("total", "ok", pytest.approx(0.5))]
    assert "agent_lb_startup_summary" in caplog.text
    assert "database" in caplog.text


@pytest.mark.asyncio
async def test_startup_recorder_observes_failed_phase_before_propagating(monkeypatch) -> None:
    observed: list[tuple[str, str, float]] = []
    monkeypatch.setattr(
        startup, "_observe_startup_phase", lambda name, outcome, seconds: observed.append((name, outcome, seconds))
    )
    recorder = startup.StartupRecorder(started_ns=0, clock_ns=_Clock(10, 40))

    with pytest.raises(RuntimeError, match="boom"):
        async with recorder.phase("database"):
            raise RuntimeError("boom")

    assert observed == [("database", "failed", 30 / 1_000_000_000)]


def test_startup_metric_helpers_are_safe_without_prometheus(monkeypatch) -> None:
    monkeypatch.setattr(startup, "_prometheus_metric", lambda _name: None)

    startup._observe_startup_phase("database", "ok", 0.25)
    startup._observe_startup_total("ok", 1.0)
    startup._observe_startup_readiness(1.1)


def test_startup_recorder_records_readiness_after_startup(monkeypatch) -> None:
    observed: list[float] = []
    monkeypatch.setattr(startup, "_observe_startup_readiness", observed.append)
    recorder = startup.StartupRecorder(started_ns=0, clock_ns=_Clock(2_000_000_000))

    assert recorder.ready() == pytest.approx(2.0)
    assert observed == [pytest.approx(2.0)]
