from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.startup_benchmark import (
    BenchmarkRecord,
    CommandSample,
    compare_records,
    percentile,
    run_command_sample,
    write_record,
)


def _record(*, median: float, p95: float) -> BenchmarkRecord:
    return BenchmarkRecord(
        schema_version=1,
        recorded_at="2026-07-11T00:00:00+00:00",
        label="ccgpt",
        mode="command",
        command="ccgpt",
        git_revision="abc123",
        platform="test",
        python="3.13",
        samples=[],
        metrics={"useful_output_seconds": {"count": 5, "median": median, "p95": p95}},
        failures=0,
    )


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([0.5, 0.1, 0.3, 0.2, 0.4], 95) == pytest.approx(0.5)
    assert percentile([], 95) is None


def test_command_sample_records_marker_time_without_persisting_output() -> None:
    sample = run_command_sample(
        [sys.executable, "-c", "print('secret prompt'); print('READY')"],
        marker="READY",
        timeout_seconds=5,
    )

    assert sample.exit_code == 0
    assert sample.useful_output_seconds is not None
    assert sample.completion_seconds >= sample.useful_output_seconds
    assert not hasattr(sample, "output")


def test_write_record_appends_jsonl_without_raw_arguments(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    record = _record(median=1.0, p95=1.2)
    record.samples.append(CommandSample(completion_seconds=1.0, useful_output_seconds=0.8, exit_code=0, error=None))

    write_record(path, record)

    payload = json.loads(path.read_text().strip())
    assert payload["command"] == "ccgpt"
    assert "argv" not in payload
    assert payload["samples"][0]["completion_seconds"] == 1.0


def test_compare_records_flags_p95_regression() -> None:
    baseline = _record(median=1.0, p95=1.0)
    current = _record(median=1.05, p95=1.25)

    comparison = compare_records(current, baseline, max_p95_regression_percent=10)

    assert comparison["useful_output_seconds"]["p95_change_percent"] == pytest.approx(25.0)
    assert comparison["regressed"] is True
