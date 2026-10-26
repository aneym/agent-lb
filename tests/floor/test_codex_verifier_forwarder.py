import re
from pathlib import Path

import pytest

AGENTS = Path(__file__).resolve().parents[2] / "config/coding-agents/agents"


def test_the_forwarder_waits_until_the_review_finishes_in_bounded_calls():
    text = (AGENTS / "codex-verifier.md").read_text()
    waits = re.findall(r"seat-run --wait K[^\n]*\n[^\n]*timeout: (\d+)", text)

    assert waits and all(int(ms) <= 300000 for ms in waits)
    assert "--wait K --max 240" in text
    assert not re.search(r"at most \d+ waits|after eight", text, re.I)


@pytest.mark.parametrize("name", ["codex-verifier.md", "codex-verifier-local.md"])
def test_the_contract_names_no_default_runner_output_path(name):
    assert ".verify-runs/pr" not in (AGENTS / name).read_text()
