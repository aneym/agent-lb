from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys

import pytest

from app.modules.team.api import _build_snippets

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("base_url", ["https://lb.example", "https://lb.example/a'b/$HOME/$(printf injected)/`id`"])
def test_zsh_onboarding_assigns_literal_urls(base_url):
    snippets = _build_snippets(base_url)
    command = (
        snippets.macos_zsh
        + "\n"
        + shlex.join(
            [
                sys.executable,
                "-c",
                "import json, os; print(json.dumps([os.environ[k] for k in "
                '["ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"]]))',
            ]
        )
    )
    result = subprocess.run(
        [shutil.which("zsh") or "/bin/sh", "-c", command], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == [base_url, f"{base_url}/v1", "<key>", "<key>"]


def test_powershell_onboarding_quotes_metacharacters_as_literal_strings():
    base_url = "https://lb.example/a'b/$HOME/$(Write-Output injected)/`n"
    snippets = _build_snippets(base_url)
    assignments = [
        line.split(" = ", 1)[1] for line in snippets.windows_powershell.splitlines() if "BASE_URL = " in line
    ]
    assert assignments == [
        "'https://lb.example/a''b/$HOME/$(Write-Output injected)/`n'",
        "'https://lb.example/a''b/$HOME/$(Write-Output injected)/`n/v1'",
    ]
