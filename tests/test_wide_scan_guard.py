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
    ("bash <<EOF\nrg x /\nEOF\n", 2),
    ("sh <<-EOF\n\trg x /\n\tEOF\n", 2),
    ("cat <<'EOF' | bash\nrg x /\nEOF\n", 2),
    ("cat <<EOF\n$(rg x /)\nEOF\n", 2),
    ("cat <<EOF\n`rg x /`\nEOF\n", 2),
    ("cat <<EOF\n'$(rg x /)'\nEOF\n", 2),
    ("cat <<EOF\n# $(rg x /)\nEOF\n", 2),
    ("cat <<'EOF'\n$(rg x /)\nEOF\n", 0),
    ('echo "two\nlines"; rg x file.log', 0),
    ("cd / && rg x src", 0),
    ("cat <<'EOF'\ntext\nrg x /", 2),
    ("rg x / 'unfinished", 2),
    ('echo "$(rg x /', 2),
    ("r\\\ng x /", 2),
    ("rg x /Volu\\\nmes/Media500", 2),
    ("cd / && rg x", 2),
    ("pushd ~ && rg x", 2),
    ("cd / && grep -r x", 2),
    ("cd / && find", 2),
    ("cd / && fd x", 2),
    ("sh -c 'cd / && rg x'", 2),
    ("sh -c 'pushd ~ && rg x'", 2),
    ("rg --files /", 2),
    ("find -E / -name foo", 2),
    ("find -x / -name foo", 2),
    ("find -f / -name foo", 2),
    ("find -H -L -P -X -d -s / -name foo", 2),
    ("time -p rg x /", 2),
    ("builtin rg x /", 2),
    ("ionice -c 3 rg x /", 2),
    ("arch -arm64 rg x /", 2),
    ("setsid --wait rg x /", 2),
    ("watch -n 2 rg x /", 2),
    ("watch 'rg x /'", 2),
    ("bash -c -- 'rg x /'", 2),
    ("echo '# wide-scan-ok' 'unfinished", 0),
])
def test_hook_command_segments(command, expected):
    result = subprocess.run(
        ["bash", str(GUARD)],
        input=json.dumps({"tool_input": {"command": command}, "cwd": CWD}),
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == expected, (command, result.stdout, result.stderr)


@pytest.mark.parametrize("command", [
    "printf '/\\n' | xargs rg x",
    'D=/; rg x "$D"',
    'rg x "${HOME:-/}"',
    "rg x //",
    "rg x /*",
    "ag x /",
    "ack x /",
    "grep --recur x /",
])
@pytest.mark.xfail(reason="Accepted residual: accident guard, not an adversarial boundary", strict=False)
def test_documented_residuals(command):
    result = subprocess.run(
        ["bash", str(GUARD)],
        input=json.dumps({"tool_input": {"command": command}, "cwd": CWD}),
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 2


@pytest.mark.skip(reason="Missing jq is an accepted residual; hook requires jq on the host")
def test_missing_jq():
    pass


def test_installed_hook_matches_source():
    """Protect delivery: the active hook must match the checked-in policy."""
    installed = Path.home() / ".claude/hooks/wide-scan-guard.sh"
    if not installed.exists():
        pytest.skip("wide-scan guard is not installed")
    result = subprocess.run(["cmp", str(GUARD), str(installed)], capture_output=True, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
