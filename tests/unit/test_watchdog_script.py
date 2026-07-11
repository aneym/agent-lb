from __future__ import annotations

import gzip
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "watchdog.sh"

LAUNCHCTL_SHIM = """#!/usr/bin/env bash
echo "$@" >> "$SHIM_CALL_LOG"
if [[ "$1" == "print" ]]; then
  [[ -f "$SHIM_BOOTSTRAPPED" ]] || exit 1
  [[ -n "${SHIM_PRINT_PID:-}" ]] && echo "    pid = ${SHIM_PRINT_PID}"
  exit 0
fi
exit 0
"""

CURL_SHIM = """#!/usr/bin/env bash
printf '%s' "${SHIM_HTTP_CODE:-200}"
"""

PS_SHIM = """#!/usr/bin/env bash
printf '%s\\n' "${SHIM_ETIME:-}"
"""


def _run_watchdog(
    tmp_path: Path,
    *,
    bootstrapped: bool,
    http_code: str = "200",
    print_pid: str = "",
    etime: str = "",
    log_max_mb: str = "256",
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("launchctl", LAUNCHCTL_SHIM), ("curl", CURL_SHIM), ("ps", PS_SHIM)):
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(0o755)

    bootstrapped_marker = tmp_path / "bootstrapped"
    if bootstrapped:
        bootstrapped_marker.touch()
    else:
        bootstrapped_marker.unlink(missing_ok=True)

    call_log = tmp_path / "calls.log"
    plist = tmp_path / "com.aneyman.agent-lb.plist"
    plist.touch()

    service_logs = f"{tmp_path / 'service.err.log'} {tmp_path / 'service.out.log'}"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "SHIM_CALL_LOG": str(call_log),
            "SHIM_BOOTSTRAPPED": str(bootstrapped_marker),
            "SHIM_HTTP_CODE": http_code,
            "SHIM_PRINT_PID": print_pid,
            "SHIM_ETIME": etime,
            "AGENT_LB_PLIST": str(plist),
            "AGENT_LB_WATCHDOG_STATE": str(tmp_path / "watchdog.state"),
            "AGENT_LB_WATCHDOG_PAUSE": str(tmp_path / "watchdog.pause"),
            "AGENT_LB_WATCHDOG_LOG": str(tmp_path / "watchdog.log"),
            "AGENT_LB_WATCHDOG_SERVICE_LOG_FILES": service_logs,
            "AGENT_LB_WATCHDOG_SERVICE_LOG_MAX_MB": log_max_mb,
        }
    )
    subprocess.run(["bash", str(SCRIPT)], env=env, check=True, capture_output=True)
    return call_log


def _bootstrap_calls(call_log: Path) -> list[str]:
    if not call_log.exists():
        return []
    return [line for line in call_log.read_text().splitlines() if line.startswith("bootstrap ")]


def _kickstart_calls(call_log: Path) -> list[str]:
    if not call_log.exists():
        return []
    return [line for line in call_log.read_text().splitlines() if line.startswith("kickstart ")]


def test_unbootstrapped_service_not_revived_on_first_tick(tmp_path: Path) -> None:
    call_log = _run_watchdog(tmp_path, bootstrapped=False)

    assert _bootstrap_calls(call_log) == []
    assert "missing=1" in (tmp_path / "watchdog.state").read_text()


def test_unbootstrapped_service_revived_on_second_consecutive_tick(tmp_path: Path) -> None:
    _run_watchdog(tmp_path, bootstrapped=False)
    call_log = _run_watchdog(tmp_path, bootstrapped=False)

    bootstraps = _bootstrap_calls(call_log)
    assert len(bootstraps) == 1
    assert bootstraps[0].endswith("com.aneyman.agent-lb.plist")
    assert "missing=0" in (tmp_path / "watchdog.state").read_text()


def test_healthy_bootstrapped_service_resets_missing_counter(tmp_path: Path) -> None:
    _run_watchdog(tmp_path, bootstrapped=False)
    _run_watchdog(tmp_path, bootstrapped=True, http_code="200")
    call_log = _run_watchdog(tmp_path, bootstrapped=False)

    assert _bootstrap_calls(call_log) == []
    assert "missing=1" in (tmp_path / "watchdog.state").read_text()


def test_pause_file_suppresses_revival(tmp_path: Path) -> None:
    _run_watchdog(tmp_path, bootstrapped=False)
    (tmp_path / "watchdog.pause").touch()
    call_log = _run_watchdog(tmp_path, bootstrapped=False)

    assert _bootstrap_calls(call_log) == []


def test_kick_skipped_while_process_within_boot_grace(tmp_path: Path) -> None:
    call_log = tmp_path / "calls.log"
    for _ in range(3):
        call_log = _run_watchdog(
            tmp_path,
            bootstrapped=True,
            http_code="000",
            print_pid="4242",
            etime="01:30",
        )

    assert _kickstart_calls(call_log) == []
    assert "boot grace" in (tmp_path / "watchdog.log").read_text()
    # counter keeps accumulating so the kick fires once the grace expires
    assert "count=3" in (tmp_path / "watchdog.state").read_text()


def test_kick_fires_when_process_older_than_boot_grace(tmp_path: Path) -> None:
    call_log = tmp_path / "calls.log"
    for _ in range(3):
        call_log = _run_watchdog(
            tmp_path,
            bootstrapped=True,
            http_code="000",
            print_pid="4242",
            etime="02:10:00",
        )

    kicks = _kickstart_calls(call_log)
    assert len(kicks) == 1
    assert "com.aneyman.agent-lb" in kicks[0]


def test_oversized_service_log_rotated_and_truncated(tmp_path: Path) -> None:
    service_log = tmp_path / "service.err.log"
    service_log.write_bytes(b"x" * (2 * 1024 * 1024))

    _run_watchdog(tmp_path, bootstrapped=True, http_code="200", log_max_mb="1")

    assert service_log.stat().st_size == 0
    rotated = tmp_path / "service.err.log.1.gz"
    assert rotated.exists()
    with gzip.open(rotated) as fh:
        assert len(fh.read()) == 2 * 1024 * 1024
