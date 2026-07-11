from __future__ import annotations

import os
import plistlib
import subprocess
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LABEL = "com.aneyman.agent-lb"


def _launch_agent_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _print_generated_plist(home: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    result = subprocess.run(
        ["bash", "scripts/install-service.sh", "--print"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    return plistlib.loads(result.stdout)


def test_install_service_plist_uses_menubar_service_label(tmp_path: Path) -> None:
    generated = _print_generated_plist(tmp_path)

    assert generated["Label"] == LABEL


def test_install_service_plist_defaults_file_limits_above_launchd_256(tmp_path: Path) -> None:
    generated = _print_generated_plist(tmp_path)

    assert generated["SoftResourceLimits"] == {"NumberOfFiles": 4096}
    assert generated["HardResourceLimits"] == {"NumberOfFiles": 8192}


def test_install_service_plist_preserves_custom_file_limits(tmp_path: Path) -> None:
    existing_path = _launch_agent_path(tmp_path)
    existing_path.parent.mkdir(parents=True)
    existing_path.write_bytes(
        plistlib.dumps(
            {
                "Label": LABEL,
                "SoftResourceLimits": {"NumberOfFiles": 2048},
                "HardResourceLimits": {"NumberOfFiles": 16384, "Stack": 67104768},
            }
        )
    )

    generated = _print_generated_plist(tmp_path)

    assert generated["SoftResourceLimits"] == {"NumberOfFiles": 2048}
    assert generated["HardResourceLimits"] == {"NumberOfFiles": 16384, "Stack": 67104768}


def test_install_service_plist_preserves_existing_runtime_configuration(tmp_path: Path) -> None:
    existing_path = _launch_agent_path(tmp_path)
    existing_path.parent.mkdir(parents=True)
    existing_path.write_bytes(
        plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": [
                    "/old/repo/.venv/bin/agent-lb",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "2455",
                ],
                "EnvironmentVariables": {
                    "PATH": "/custom/bin:/usr/bin:/bin",
                    "AGENT_LB_DATABASE_URL": "postgresql+asyncpg://agent_lb:agent_lb@127.0.0.1:5432/agent_lb",
                    "AGENT_LB_DASHBOARD_AUTH_MODE": "disabled",
                    "AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS": (
                        "127.0.0.1/32,::1/128,100.64.0.0/10,fd7a:115c:a1e0::/48"
                    ),
                    "AGENT_LB_METRICS_PORT": "9191",
                },
                "SoftResourceLimits": {"NumberOfFiles": 4096},
                "HardResourceLimits": {"NumberOfFiles": 8192},
            }
        )
    )

    generated = _print_generated_plist(tmp_path)

    assert generated["ProgramArguments"] == [
        str(ROOT / ".venv" / "bin" / "agent-lb"),
        "--host",
        "127.0.0.1",
        "--port",
        "2455",
    ]
    assert generated["SoftResourceLimits"] == {"NumberOfFiles": 4096}
    assert generated["HardResourceLimits"] == {"NumberOfFiles": 8192}

    env = generated["EnvironmentVariables"]
    assert env["PATH"] == "/custom/bin:/usr/bin:/bin"
    assert env["AGENT_LB_DATABASE_URL"] == "postgresql+asyncpg://agent_lb:agent_lb@127.0.0.1:5432/agent_lb"
    assert env["AGENT_LB_DASHBOARD_AUTH_MODE"] == "disabled"
    assert env["AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS"] == (
        "127.0.0.1/32,::1/128,100.64.0.0/10,fd7a:115c:a1e0::/48"
    )
    assert env["AGENT_LB_METRICS_ENABLED"] == "true"
    assert env["AGENT_LB_METRICS_HOST"] == "127.0.0.1"
    assert env["AGENT_LB_METRICS_PORT"] == "9191"


def test_install_service_metrics_default_has_runtime_dependency() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    default_dependencies = set(pyproject["project"]["dependencies"])

    assert "prometheus-client>=0.20" in default_dependencies


LAUNCHCTL_SHIM = """#!/usr/bin/env bash
echo "$@" >> "$SHIM_CALL_LOG"
exit 0
"""

# Always reports a listener on 127.0.0.1:2455 — the old process never drains.
LSOF_SHIM = """#!/usr/bin/env bash
echo "python  1234 user  23u  IPv4  TCP 127.0.0.1:2455 (LISTEN)"
"""

CURL_SHIM = """#!/usr/bin/env bash
echo '{"status":"ok"}'
"""


def test_install_service_busy_port_after_bootout_still_bootstraps(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("launchctl", LAUNCHCTL_SHIM), ("lsof", LSOF_SHIM), ("curl", CURL_SHIM)):
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    call_log = tmp_path / "calls.log"

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "SHIM_CALL_LOG": str(call_log),
            "AGENT_LB_INSTALL_PORT_FREE_TIMEOUT_SECONDS": "1",
        }
    )
    result = subprocess.run(
        ["bash", "scripts/install-service.sh"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "bootstrapping anyway" in result.stderr
    calls = call_log.read_text()
    assert "bootout " in calls
    assert "bootstrap " in calls
    assert _launch_agent_path(home).exists()


def test_install_service_restart_is_readiness_driven_and_timed() -> None:
    script = (ROOT / "scripts" / "install-service.sh").read_text()

    assert 'curl -fsS "http://127.0.0.1:$PORT/health/ready"' in script
    assert "startup_timing_ms" in script
    assert "cooldown_remaining" not in script
    assert 'sleep 0.1' in script
    assert 'if label_loaded; then' in script
    assert 'bootstrap_ok=true' in script
    assert 'READY_TIMEOUT_SECONDS="${AGENT_LB_INSTALL_READY_TIMEOUT_SECONDS:-120}"' in script
    assert 'deadline=$(($(date +%s) + READY_TIMEOUT_SECONDS))' in script
    assert 'LaunchAgent disappeared during readiness; re-bootstrap attempt' in script
