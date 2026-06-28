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
