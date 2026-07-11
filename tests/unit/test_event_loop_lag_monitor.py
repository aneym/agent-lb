from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import app.core.resilience.event_loop_lag_monitor as lag_monitor

pytestmark = pytest.mark.unit


def test_build_event_loop_lag_monitor_uses_configured_threshold() -> None:
    monitor = lag_monitor.build_event_loop_lag_monitor(warning_threshold_seconds=1.5)

    assert monitor.warning_threshold_seconds == 1.5
    assert monitor.check_interval_seconds == 1.0


def test_record_lag_updates_gauge_without_crossing_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    gauge = MagicMock()
    counter = MagicMock()
    monkeypatch.setattr(lag_monitor.metrics, "event_loop_lag_seconds", gauge)
    monkeypatch.setattr(lag_monitor.metrics, "event_loop_lag_events_total", counter)

    monitor = lag_monitor.EventLoopLagMonitor(warning_threshold_seconds=0.5)
    monitor._record_lag(0.1)

    gauge.set.assert_called_once_with(0.1)
    counter.inc.assert_not_called()


def test_record_lag_logs_and_increments_counter_when_over_threshold(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    gauge = MagicMock()
    counter = MagicMock()
    monkeypatch.setattr(lag_monitor.metrics, "event_loop_lag_seconds", gauge)
    monkeypatch.setattr(lag_monitor.metrics, "event_loop_lag_events_total", counter)

    monitor = lag_monitor.EventLoopLagMonitor(warning_threshold_seconds=0.5)
    with caplog.at_level("WARNING", logger=lag_monitor.__name__):
        monitor._record_lag(0.9)

    gauge.set.assert_called_once_with(0.9)
    counter.inc.assert_called_once()
    assert "event_loop_lag lag=0.900s" in caplog.text


_RATE_LIMIT_SECONDS = lag_monitor._WARNING_RATE_LIMIT_SECONDS


def test_record_lag_rate_limits_repeated_warnings(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    gauge = MagicMock()
    counter = MagicMock()
    monkeypatch.setattr(lag_monitor.metrics, "event_loop_lag_seconds", gauge)
    monkeypatch.setattr(lag_monitor.metrics, "event_loop_lag_events_total", counter)

    fake_now = [1000.0]
    monkeypatch.setattr(lag_monitor.time, "monotonic", lambda: fake_now[0])

    monitor = lag_monitor.EventLoopLagMonitor(warning_threshold_seconds=0.5)
    with caplog.at_level("WARNING", logger=lag_monitor.__name__):
        monitor._record_lag(0.9)
        fake_now[0] += 1.0
        monitor._record_lag(0.9)

    assert counter.inc.call_count == 2
    assert caplog.text.count("event_loop_lag lag=") == 1

    fake_now[0] += _RATE_LIMIT_SECONDS
    with caplog.at_level("WARNING", logger=lag_monitor.__name__):
        monitor._record_lag(0.9)
    assert caplog.text.count("event_loop_lag lag=") == 2


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle_runs_and_cancels_cleanly() -> None:
    monitor = lag_monitor.EventLoopLagMonitor(warning_threshold_seconds=0.5, check_interval_seconds=0.01)

    await monitor.start()
    assert monitor._task is not None and not monitor._task.done()

    await monitor.stop()
    assert monitor._task is None
