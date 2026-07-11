from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "watchdog.sh"

LAUNCHCTL_SHIM = """#!/usr/bin/env bash
echo "$@" >> "$SHIM_CALL_LOG"
if [[ "$1" == "print" ]]; then
  [[ -f "$SHIM_BOOTSTRAPPED" ]] && exit 0 || exit 1
fi
exit 0
"""

CURL_SHIM = """#!/usr/bin/env bash
printf '%s' "${SHIM_HTTP_CODE:-200}"
"""


def _run_watchdog(tmp_path: Path, *, bootstrapped: bool, http_code: str = "200") -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("launchctl", LAUNCHCTL_SHIM), ("curl", CURL_SHIM)):
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

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "SHIM_CALL_LOG": str(call_log),
            "SHIM_BOOTSTRAPPED": str(bootstrapped_marker),
            "SHIM_HTTP_CODE": http_code,
            "AGENT_LB_PLIST": str(plist),
            "AGENT_LB_WATCHDOG_STATE": str(tmp_path / "watchdog.state"),
            "AGENT_LB_WATCHDOG_PAUSE": str(tmp_path / "watchdog.pause"),
            "AGENT_LB_WATCHDOG_LOG": str(tmp_path / "watchdog.log"),
        }
    )
    subprocess.run(["bash", str(SCRIPT)], env=env, check=True, capture_output=True)
    return call_log


def _bootstrap_calls(call_log: Path) -> list[str]:
    if not call_log.exists():
        return []
    return [line for line in call_log.read_text().splitlines() if line.startswith("bootstrap ")]


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
