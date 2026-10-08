"""Exercise the real Bash hook with PreToolUse payloads; commands are never executed."""
import json
import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "config/coding-agents/hooks/wide-scan-guard.sh"
CWD = str(Path.home() / ".agent-rails/agents/home")


@pytest.mark.parametrize("command,expected", [
    ("grep -rh -o x ~/Library/foo/*/options.xml 2>/dev/null | sort -u", 0),
    ("grep -rh -o x ~/Library/foo/*/options.xml | sort -u", 0),
    ('ls /Volumes/; rg -i err "$J/log/a.log" | tail', 0),
    ('ls "/Volumes/Media500/Library/TV Shows/" | rg -i angel', 0),
    ("diskutil info /Volumes/Media500 | rg -i protocol", 0),
    ("rg foo /Volumes/Media500", 2),
    ('echo "rg foo /Volumes/Media500; rg x ~/.agent-rails/lanes"', 0),
    ('lane-post post --kind info "rg foo /Volumes/Media500; rg x ~/.agent-rails"', 0),
    ("cat <<'EOF'\nrg foo /Volumes/Media500\nrg x ~/.agent-rails/lanes\nEOF\n", 0),
    ("cat <<EOF\nrg foo /Volumes/Media500\nEOF\nrg x file.log", 0),
    ("cat <<-'EOF'\n\trg foo /Volumes/Media500\n\tEOF\n", 0),
    ("cat <<'EOF'\nrg foo /Volumes/Media500\nEOF\nrg x /Volumes/Media500", 2),
    ("printf x | rg foo /Volumes/Media500 | head", 2),
    ("rg foo /", 2),
    ("rg foo ~", 2),
    ("find ~/.agent-rails/lanes -name STATE.md", 2),
    ("rg foo ~/.agent-rails", 2),
    ('echo "$(rg foo /Volumes/Media500)"', 2),
    ("bash -c 'rg foo /Volumes/Media500'", 2),
    ("grep -r x ~/.agent-rails/agents/home 2>/dev/null", 2),
    ("grep -r -D skip x ~/.agent-rails/agents/home 2>/dev/null", 0),
    ("rg foo /Volumes/Media500 # wide-scan-ok", 0),
])
def test_hook_command_segments(command, expected):
    result = subprocess.run(
        ["bash", str(GUARD)],
        input=json.dumps({"tool_input": {"command": command}, "cwd": CWD}),
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == expected, (command, result.stdout, result.stderr)
