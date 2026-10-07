"""Exercise the public CLI guard before it can reach launchd or Postgres."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/lb-reboot-drill"


@pytest.mark.parametrize(
    ("label", "port", "accepted"),
    [
        ("com.agent-lb.drill.test", "2470", True),
        ("com.agent-lb.live", "2470", False),
        ("com.aneyman.agent-lb", "2470", False),
        ("com.agent-lb.drill.test", "2455", False),
        ("com.agent-lb.drill.test", "65536", False),
        ("com.agent-lb.drill../live", "2470", False),
    ],
)
def test_sandbox_guard_cli(label, port, accepted):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--guard-only", "--label", label, "--port", port],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) is accepted, result.stderr
