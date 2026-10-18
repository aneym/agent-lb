import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("model", ["astra-latest", "astra-latest-high", "gpt-6-astra", "astra-latest-invalid"])
def test_doctor_validates_astra_alias_at_cli_boundary(tmp_path, model):
    home = tmp_path / "home"
    agents = home / ".claude" / "agents"
    agents.mkdir(parents=True)
    definition = agents / "astra-consult.md"
    content = f"---\nname: astra-consult\ndescription: Architecture consultation\nmodel: {model}\ntools: '*'\n---\n"
    definition.write_text(content)
    cwd = tmp_path / "project"
    cwd.mkdir()
    doctor = Path(__file__).resolve().parents[2] / "clients" / "agent-defs-doctor"
    completed = subprocess.run(
        [
            sys.executable,
            str(doctor),
            "--repair",
            "--json",
            "--user-root",
            str(home),
            "--cwd",
            str(cwd),
            "--state-dir",
            str(tmp_path / "state"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    result = json.loads(completed.stdout)
    invalid = model == "astra-latest-invalid"
    assert completed.returncode == int(invalid), completed.stdout + completed.stderr
    assert result["summary"]["unresolved"] == int(invalid)
    assert result["summary"]["repaired"] == 0
    assert result["files"][0]["status"] == ("error" if invalid else "ok")
    assert definition.read_text() == content
