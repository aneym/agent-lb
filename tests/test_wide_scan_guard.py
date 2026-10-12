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
    ("grep -r x ~/.agent-rails/agents/home 2>/dev/null", 1),
    ("grep -r -D skip x ~/.agent-rails/agents/home 2>/dev/null", 0),
    ("rg foo /Volumes/Media500 # wide-scan-ok", 2),  # the comment silences a warning, never a refusal
    ("rg foo /Volumes/StudioExt/repos # wide-scan-ok", 0),
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
    ("cat <<'EOF'\ntext\nrg x /", 1),
    ("rg x / 'unfinished", 1),
    ('echo "$(rg x /', 1),
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
    ('git commit -m "$(cat <<\'EOF\'\nfix: restore wide scan guard\n\nCo-Authored-By: Claude Code <noreply@anthropic.com>\nEOF\n)"', 0),
    ('git commit -m "$(cat <<EOF\nfix: allow commit heredocs\nEOF\n)"', 0),
    ('gh pr create --body "$(cat <<\'EOF\'\nSummary: preserve quoted heredoc data\nEOF\n)"', 0),
    ('gh pr create --body "$(cat <<EOF\nSummary: restore the hook\nEOF\n)"', 0),
    ('x="$(cat <<\'EOF\'\nrg x /\nEOF\n)"', 0),
    ('x="$(cat <<EOF\nrg x /\nEOF\n)"', 0),
    ('x="$(cat <<EOF\n$(rg x /)\nEOF\n)"', 2),
    ('echo "literal <<EOF"', 0),
    ("bash <<< 'rg x /'", 2),
    ("sh <<< 'rg x /'", 2),
    ("bash <<< 'rg x src'", 0),
    # factory-burn 2026-10-12: the scans that took Studio to 0% idle, bare and through rtk (rtk grep is recursive).
    ("grep -rl 0123abcd ~/.agent-rails/agents ~/.agent-rails/evidence", 2),
    ("grep -rl 38280104567 ~/.agent-rails --include=*.jsonl", 2),
    ("rtk grep -rl 0123abcd ~/.agent-rails/agents ~/.agent-rails/evidence", 2),
    ("rtk grep x ~/.agent-rails", 2),
    ("rtk grep x ~/.agent-rails/lanes", 2),
    ("rtk -v grep x /Volumes/StudioExt", 2),
    ("rtk find ~ -name x", 2),
    ("rtk proxy grep -r x ~", 2),
    ("rtk run -c 'rg x /'", 2),
    ("rg -n x ~/.agent-rails/evidence", 2),
    ("rg -n x /Users/aneyman", 2),
    ("cd ~/.agent-rails && grep -r x", 2),
    # one lane, one agent dir or a repo checkout stays allowed
    ("rg -n x ~/.agent-rails/lanes/factory-burn-2026-10-09", 0),
    ("rtk grep x ~/.agent-rails/lanes/factory-burn-2026-10-09", 0),
    ("rtk grep -rl x ~/.agent-rails/agents/home", 0),
    ("rg -n x /Volumes/StudioExt/repos/agent-lb", 0),
    ("rtk grep x src", 0),
    ("rtk git status", 0),
    # other wide roots only warn
    ("rg x /Volumes/StudioExt/repos", 1),
    ("rtk grep x /System", 1),
])
def test_hook_command_segments(command, expected):
    """expected 2: refused (exit 2, one BLOCKED line on stderr), for a recursive search over /, $HOME, a volume root
    or ~/.agent-rails (or its agents, evidence or lanes dir) as a whole (factory-burn 2026-10-12). expected 1: exit 0
    with one warn-only line of context (other wide roots, unparsed input, a FIFO grep it cannot rewrite). 0: silent."""
    result = run(command)
    if expected == 2:
        assert result.returncode == 2, (command, result.stdout, result.stderr)
        assert result.stderr.startswith("BLOCKED: wide-scan-guard: recursive "), result.stderr
        assert len(result.stderr.strip().splitlines()) == 1, result.stderr
        return
    assert result.returncode == 0, (command, result.stdout, result.stderr)
    assert warned(result) is (expected == 1), (command, result.stdout, result.stderr)


@pytest.mark.parametrize("command,replacement", [
    ("grep -rl 0123abcd ~/.agent-rails/agents", 'Use: rg -n "0123abcd" ~/.agent-rails/<one dir>'),
    ("rg -n b-20261012171717-76059f49d8a72a41 ~/.agent-rails/lanes",
     "Use: lane-post read b-20261012171717-76059f49d8a72a41"),
    ("grep -rl 38280104567 ~/.agent-rails --include=*.jsonl", "lane-post read 38280104567 for a bulletin"),
    ("rg x /", 'Use: rg -n "x" <one repo or dir>'),
])
def test_refusal_names_the_rule_and_the_replacement(command, replacement):
    result = run(command)
    assert result.returncode == 2
    assert "house-tools rule: search one directory under ~/.agent-rails" in result.stderr
    assert replacement in result.stderr, result.stderr


def run(command):
    return subprocess.run(
        ["bash", str(GUARD)],
        input=json.dumps({"tool_input": {"command": command}, "cwd": CWD}),
        text=True, capture_output=True, timeout=10,
    )


def warned(result):
    if not result.stdout.strip():
        return False
    out = json.loads(result.stdout)["hookSpecificOutput"]
    if "updatedInput" in out:  # the FIFO grep rewrite (-D skip) is a fix, not a warning
        return False
    assert "permissionDecision" not in out
    return out["additionalContext"].startswith("wide-scan-guard (warn only, not blocked): ")


@pytest.mark.parametrize("command", [
    "printf '/\\n' | xargs rg x",
    'D=/; rg x "$D"',
    'rg x "${HOME:-/}"',
    "rg x //",
    "rg x /*",
    "ag x /",
    "ack x /",
    "grep --recur x /",
    "echo 'rg x /' | bash",
    "eval 'rg x /'",
    "source <(echo 'rg x /')",
])
@pytest.mark.xfail(reason="Accepted residual: accident guard, not an adversarial boundary", strict=False)
def test_documented_residuals(command):
    assert warned(run(command))


# Missing or failing jq (and a failing scanner) lets the call through with a warning (guard trim, 2026-10-08).


def test_installed_hook_matches_source():
    """Protect delivery: the active hook must match the checked-in policy."""
    installed = Path.home() / ".claude/hooks/wide-scan-guard.sh"
    if not installed.exists():
        pytest.skip("wide-scan guard is not installed")
    result = subprocess.run(["cmp", str(GUARD), str(installed)], capture_output=True, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
