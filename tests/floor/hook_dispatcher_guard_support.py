from __future__ import annotations

import json
import shutil
from pathlib import Path

from tests.floor.hook_dispatcher_support import (
    SOURCE,
    dispatcher_home,
)

DANGEROUS = (
    'bash -c \'CMD=$(cat | jq -r ".tool_input.command // empty"); [ -z "$CMD" ] && exit 0; '
    'echo "$CMD" | grep -qiE "rm\\s+-rf\\s+/|DROP\\s+(DATABASE|TABLE)" && { echo "BLOCKED: '
    "Dangerous command requires explicit user approval.\" >&2; exit 2; }; exit 0'"
)

FLOOR_LEAVES = {
    "rm-dynamic-deny": (
        '/usr/bin/python3 "$HOME/.local/bin/rm-dynamic-deny"',
        ".local/bin/rm-dynamic-deny",
        "py",
    ),
    "stash-guard": (
        '/usr/bin/python3 "$HOME/.claude/hooks/stash-guard"',
        ".claude/hooks/stash-guard",
        "py",
    ),
    "host-credential-guard.py": (
        '/usr/bin/python3 "$HOME/.claude/hooks/host-credential-guard.py"',
        ".claude/hooks/host-credential-guard.py",
        "py",
    ),
    "link-cli-guard.sh": ('"$HOME/.claude/hooks/link-cli-guard.sh"', ".claude/hooks/link-cli-guard.sh", "sh"),
    "railway-vars-guard.sh": (
        '"$HOME/.claude/hooks/railway-vars-guard.sh"',
        ".claude/hooks/railway-vars-guard.sh",
        "sh",
    ),
    "plutil-guard.sh": ('"$HOME/.claude/hooks/plutil-guard.sh"', ".claude/hooks/plutil-guard.sh", "sh"),
    "dangerous-command-guard.sh": (
        '"$HOME/.claude/hooks/dangerous-command-guard.sh"',
        ".claude/hooks/dangerous-command-guard.sh",
        "sh",
    ),
    "dangerous-command-guard.sh:legacy inline": (DANGEROUS, ".claude/hooks/dangerous-command-guard.sh", "sh"),
    "dangerous-command:inline copy": (DANGEROUS + " ", None, "sh"),
    "wide-scan-guard.sh": ('"$HOME/.claude/hooks/wide-scan-guard.sh"', ".claude/hooks/wide-scan-guard.sh", "sh"),
    "agent-lb-bootout-guard.sh": (
        '"$HOME/.claude/hooks/agent-lb-bootout-guard.sh"',
        ".claude/hooks/agent-lb-bootout-guard.sh",
        "sh",
    ),
}

FAIL_STUBS = {
    "py": {
        "timeout": "import time\ntime.sleep(30)\n",
        "crash": "raise RuntimeError('synthetic floor crash')\n",
        "malformed": "import sys\nsys.stdin.read()\nprint('not a hook answer {')\n",
        "no-receipt": "import sys\nsys.stdin.read()\n",
    },
    "sh": {
        "timeout": "#!/bin/bash\nexec sleep 30\n",
        "crash": "#!/bin/bash\necho 'synthetic crash' >&2\nexit 1\n",
        "missing": '#!/bin/bash\necho "bash: $0: No such file or directory" >&2\nexit 127\n',
        "malformed": "#!/bin/bash\ncat >/dev/null\necho 'not a hook answer {'\n",
        "no-receipt": "#!/bin/bash\ncat >/dev/null\nexit 0\n",
    },
}

REACHES_PREFILTERS = "echo railway link-cli launchctl; rm -rf /tmp/never-run"

INPROC = ["rm-dynamic-deny", "stash-guard", "host-credential-guard.py"]


def failing_guard_home(tmp_path: Path, command: str, rel: str | None, kind: str, mode: str) -> tuple[Path, dict]:
    hook = {"type": "command", "command": command, "timeout": 1 if mode == "timeout" else 20}
    home, env = dispatcher_home(tmp_path, [hook])
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    (home / ".claude/hooks/dispatch/registry.json").write_text(json.dumps(dict(registry, inproc=INPROC)))
    if rel is None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "bash").write_text(FAIL_STUBS["sh"][mode])
        (bin_dir / "bash").chmod(0o755)
        env = env | {"PATH": f"{bin_dir}:{env['PATH']}"}
    elif mode == "missing":
        (home / rel).unlink(missing_ok=True)
    else:
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(("#!/usr/bin/env python3\n" if kind == "py" else "") + FAIL_STUBS[kind][mode])
        path.chmod(0o755)
    return home, env


WIDE_SCAN = SOURCE / "hooks/wide-scan-guard.sh"

WIDE_SCAN_COMMANDS = (
    "rg -n foo src",
    "find . -name '*.py'",
    "ls -la",
    "git status",
    "rg x /tmp/project",
    "find /tmp -name x",
    "rg foo .",
    "rg foo ..",
    "find ..",
    "fd x ../..",
    "cd .. && rg foo .",
    "cd / && rg x src",
    "cd src && cd .. && cd .. && rg x .",
    "rg x /",
    "rg x ~",
    "rg x $HOME",
    'rg x "$HOME/repos"',
    "grep -r x .",
    "grep -rn x ~/.agent-rails/lanes",
    "grep -r x ~/.agent-rails/agents/home 2>/dev/null",
    "rg x ~/.agent-rails",
    "fd x /Volumes/Media500",
    "bash -c 'cd ~ && rg x .'",
    "env -S'rg x /'",
    'echo "$(find ~ -name x)"',
    'rg x "/Volumes/StudioExt/repos/a b/../.."',
    "rg x '/tmp/a b/../../..'",
    "cat <<'EOF'\nrg x /\nEOF",
    "xargs grep -r x ..",
    "printf x | rg foo /Volumes/Media500 | head",
    "rg foo",
    "cd && rg x .",
    "pushd / && rg x .",
    "find -f / -name x",
    "rg --files /",
    "watch " * 9 + "ls",
    'echo "unbalanced',
    "bash -c 'echo \"x'",
    "cat <<EOF\nx",
    'echo x #" \necho "y',
    "ls -la # it's fine",
    "python3 -c 'print(1)'",
    "bash <<< 'rg x /'",
    "cat <<< 'rg x /'",
    "git commit -F - <<'EOF'\nrg x /\nEOF",
    "bash <<EOF\nrg x /\nEOF",
    "grep -rl x ~/.agent-rails/agents ~/.agent-rails/evidence",
    "rtk grep x ~/.agent-rails",
    "rtk grep x src",
    "rtk proxy grep -r x ~",
    "rtk run -c 'rg x /'",
    "rtk grep x ~/.agent-rails/lanes/a",
    "rtk git status",
    "grep -rln x .",
    "rtk grep -rln cost_cmd --include=*.py .",
    "rg x /Volumes/StudioExt/repos",
    "grep -rln x /Volumes/StudioExt/repos/agent-lb-wt",
    "rtk rg x /Volumes/StudioExt/repos/factory-wt/",
    "rg -n x /Volumes/StudioExt/repos/agent-lb-wt/topic",
    "grep -rln x ..",
)

BOOTOUT = SOURCE / "hooks/agent-lb-bootout-guard.sh"

RAW_RESTART = "launchctl kickstart -k gui/501/com.aneyman.agent-lb"


def guard_home(tmp_path: Path, name: str, source: Path, timeout: float = 20) -> tuple[Path, dict, dict]:

    hook = {"type": "command", "command": f'"$HOME/.claude/hooks/{name}"', "timeout": timeout}
    home, env = dispatcher_home(tmp_path, [hook])
    shutil.copy(source, home / ".claude/hooks" / name)
    (home / ".claude/hooks" / name).chmod(0o755)
    return home, env, hook


def stub_bin(tmp_path: Path, env: dict, name: str, text: str) -> dict:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / name).write_text(text)
    (bin_dir / name).chmod(0o755)
    return env | {"PATH": f"{bin_dir}:{env['PATH']}"}


RAILWAY = SOURCE / "hooks/railway-vars-guard.sh"

RAILWAY_PAYLOADS = [
    {"command": c}
    for c in (
        "ls -la",
        "git status",
        "railway status",
        "railway variables --set KEY=v",
        "npx @railway/cli up",
        "echo railway",
        "RAILWAY_TOKEN=x railway up",
        "bash -c 'railway up'",
        "cat <<EOF\nrailway up\nEOF",
        "echo $(railway up)",
        "railwayish thing",
        "grep -r railway docs",
    )
] + [{"command": None}, {"command": 5}, {}, None, "text"]

DANGER = SOURCE / "hooks/dangerous-command-guard.sh"

DROP_DB = 'psql -c "DROP DATABASE app"'


def tools_without(
    tmp_path: Path, env: dict, missing: str, names=("bash", "sh", "cat", "jq", "grep", "python3", "env", "printf")
) -> dict:

    tools = tmp_path / f"no-{missing}"
    tools.mkdir()
    for name in names:
        found = shutil.which(name, path=env["PATH"])
        if name != missing and found:
            (tools / name).symlink_to(found)
    return env | {"PATH": str(tools)}
