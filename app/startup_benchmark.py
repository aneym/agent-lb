from __future__ import annotations

import argparse
import json
import math
import os
import platform
import selectors
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any, Sequence


@dataclass(slots=True)
class CommandSample:
    completion_seconds: float
    useful_output_seconds: float | None
    exit_code: int | None
    error: str | None


@dataclass(slots=True)
class ServiceSample:
    startup_seconds: float | None
    ready_seconds: float | None
    exit_code: int | None
    error: str | None


@dataclass(slots=True)
class BenchmarkRecord:
    schema_version: int
    recorded_at: str
    label: str
    mode: str
    command: str
    git_revision: str | None
    platform: str
    python: str
    samples: list[CommandSample | ServiceSample]
    metrics: dict[str, dict[str, float | int | None]]
    failures: int


def percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percent / 100) * len(ordered)) - 1)
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "median": median(values) if values else None,
        "p95": percentile(values, 95),
    }


def run_command_sample(command: Sequence[str], *, marker: str | None, timeout_seconds: float) -> CommandSample:
    started = time.perf_counter()
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    os.set_blocking(process.stdout.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    marker_bytes = marker.encode() if marker is not None else None
    recent = b""
    useful_output_seconds: float | None = None
    error: str | None = None
    deadline = started + timeout_seconds
    try:
        while True:
            now = time.perf_counter()
            if now >= deadline:
                error = "timeout"
                process.terminate()
                break
            for key, _ in selector.select(timeout=min(0.05, deadline - now)):
                try:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    chunk = b""
                if chunk and marker_bytes is not None and useful_output_seconds is None:
                    recent = (recent + chunk)[-65536:]
                    if marker_bytes in recent:
                        useful_output_seconds = time.perf_counter() - started
            if process.poll() is not None:
                break
    finally:
        selector.close()
        process.stdout.close()
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    completion_seconds = time.perf_counter() - started
    if marker is None and error is None:
        useful_output_seconds = completion_seconds
    if error is None and process.returncode != 0:
        error = f"exit_{process.returncode}"
    return CommandSample(
        completion_seconds=completion_seconds,
        useful_output_seconds=useful_output_seconds,
        exit_code=process.returncode,
        error=error,
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def run_service_sample(*, timeout_seconds: float, data_dir: Path | None = None) -> ServiceSample:
    if data_dir is not None:
        data_dir.mkdir(parents=True, exist_ok=True)
        return _run_service_sample_in_dir(timeout_seconds=timeout_seconds, data_dir=data_dir)
    with tempfile.TemporaryDirectory(prefix="agent-lb-startup-") as raw_data_dir:
        return _run_service_sample_in_dir(timeout_seconds=timeout_seconds, data_dir=Path(raw_data_dir))


def _run_service_sample_in_dir(*, timeout_seconds: float, data_dir: Path) -> ServiceSample:
    port = _free_port()
    started = time.perf_counter()
    startup_seconds: float | None = None
    ready_seconds: float | None = None
    environment = os.environ.copy()
    environment.update(
        {
            "AGENT_LB_DATA_DIR": str(data_dir),
            "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{data_dir / 'store.db'}",
            "AGENT_LB_METRICS_ENABLED": "false",
            "AGENT_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "app.cli", "--host", "127.0.0.1", "--port", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    deadline = started + timeout_seconds
    error: str | None = None
    try:
        while time.perf_counter() < deadline:
            elapsed = time.perf_counter() - started
            if startup_seconds is None and _probe(f"http://127.0.0.1:{port}/health/startup"):
                startup_seconds = elapsed
            if _probe(f"http://127.0.0.1:{port}/health/ready"):
                ready_seconds = time.perf_counter() - started
                if startup_seconds is None:
                    startup_seconds = ready_seconds
                break
            if process.poll() is not None:
                error = f"exit_{process.returncode}"
                break
            time.sleep(0.025)
        if ready_seconds is None and error is None:
            error = "timeout"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return ServiceSample(
        startup_seconds=startup_seconds,
        ready_seconds=ready_seconds,
        exit_code=process.returncode,
        error=error,
    )


def _git_revision() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _build_record(label: str, mode: str, command: str, samples: list[CommandSample | ServiceSample]) -> BenchmarkRecord:
    metrics: dict[str, dict[str, float | int | None]] = {}
    if mode == "command":
        command_samples = [sample for sample in samples if isinstance(sample, CommandSample)]
        metrics["completion_seconds"] = _summary([sample.completion_seconds for sample in command_samples])
        metrics["useful_output_seconds"] = _summary(
            [sample.useful_output_seconds for sample in command_samples if sample.useful_output_seconds is not None]
        )
    else:
        service_samples = [sample for sample in samples if isinstance(sample, ServiceSample)]
        metrics["startup_seconds"] = _summary(
            [sample.startup_seconds for sample in service_samples if sample.startup_seconds is not None]
        )
        metrics["ready_seconds"] = _summary(
            [sample.ready_seconds for sample in service_samples if sample.ready_seconds is not None]
        )
    return BenchmarkRecord(
        schema_version=1,
        recorded_at=datetime.now(UTC).isoformat(),
        label=label,
        mode=mode,
        command=command,
        git_revision=_git_revision(),
        platform=f"{platform.system()}-{platform.machine()}",
        python=platform.python_version(),
        samples=samples,
        metrics=metrics,
        failures=sum(sample.error is not None for sample in samples),
    )


def write_record(path: Path, record: BenchmarkRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n")


def _read_latest(path: Path, *, label: str, mode: str) -> BenchmarkRecord | None:
    if not path.exists():
        return None
    latest: BenchmarkRecord | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("label") == label and payload.get("mode") == mode:
            latest = BenchmarkRecord(**payload)
    return latest


def compare_records(
    current: BenchmarkRecord,
    baseline: BenchmarkRecord,
    *,
    max_p95_regression_percent: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {"regressed": False}
    for metric, current_values in current.metrics.items():
        baseline_values = baseline.metrics.get(metric)
        if baseline_values is None:
            continue
        item: dict[str, float | None] = {}
        for aggregate in ("median", "p95"):
            current_value = current_values.get(aggregate)
            baseline_value = baseline_values.get(aggregate)
            change = None
            if isinstance(current_value, (int, float)) and isinstance(baseline_value, (int, float)) and baseline_value:
                change = ((current_value - baseline_value) / baseline_value) * 100
            item[f"{aggregate}_change_percent"] = change
        result[metric] = item
        p95_change = item["p95_change_percent"]
        if p95_change is not None and p95_change > max_p95_regression_percent:
            result["regressed"] = True
    return result


def _default_history_path() -> Path:
    data_dir = Path(os.environ.get("AGENT_LB_DATA_DIR") or (Path.home() / ".agent-lb"))
    return data_dir / "startup-benchmarks.jsonl"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure agent-lb service and launcher startup over time.")
    parser.add_argument("--history", type=Path, default=_default_history_path())
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--compare-latest", action="store_true")
    parser.add_argument("--max-p95-regression-percent", type=float, default=10.0)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    command = subparsers.add_parser("command", help="Measure a command until completion or a useful-output marker.")
    command.add_argument("--label", required=True)
    command.add_argument("--samples", type=int, default=5)
    command.add_argument("--timeout", type=float, default=30.0)
    command.add_argument("--marker")
    command.add_argument("command", nargs=argparse.REMAINDER)

    service = subparsers.add_parser("service", help="Measure an isolated local agent-lb process.")
    service.add_argument("--label", default="agent-lb-isolated")
    service.add_argument("--samples", type=int, default=5)
    service.add_argument("--timeout", type=float, default=60.0)
    service.add_argument(
        "--warm-database",
        action="store_true",
        help="Pre-initialize and reuse one isolated database to measure already-current restarts.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.samples < 1:
        raise SystemExit("--samples must be at least 1")
    baseline = _read_latest(args.history, label=args.label, mode=args.mode) if args.compare_latest else None
    if args.mode == "command":
        command = list(args.command)
        if command[:1] == ["--"]:
            command = command[1:]
        if not command:
            raise SystemExit("command mode requires a command after --")
        samples: list[CommandSample | ServiceSample] = [
            run_command_sample(command, marker=args.marker, timeout_seconds=args.timeout) for _ in range(args.samples)
        ]
        command_label = Path(command[0]).name
    else:
        if args.warm_database:
            with tempfile.TemporaryDirectory(prefix="agent-lb-startup-suite-") as raw_data_dir:
                data_dir = Path(raw_data_dir)
                warmup = run_service_sample(timeout_seconds=args.timeout, data_dir=data_dir)
                if warmup.error is not None:
                    raise SystemExit(f"service warmup failed: {warmup.error}")
                samples = [
                    run_service_sample(timeout_seconds=args.timeout, data_dir=data_dir) for _ in range(args.samples)
                ]
        else:
            samples = [run_service_sample(timeout_seconds=args.timeout) for _ in range(args.samples)]
        command_label = "python -m app.cli"
    record = _build_record(args.label, args.mode, command_label, samples)
    comparison = None
    if baseline is not None:
        comparison = compare_records(
            record,
            baseline,
            max_p95_regression_percent=args.max_p95_regression_percent,
        )
    if not args.no_write:
        write_record(args.history, record)
    output = {
        "record": asdict(record),
        "history": None if args.no_write else str(args.history),
        "comparison": comparison,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    if record.failures:
        raise SystemExit(1)
    if comparison is not None and comparison["regressed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
