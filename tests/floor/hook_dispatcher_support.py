from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]

SOURCE = ROOT / "config/coding-agents"
sys.path.insert(0, str(SOURCE / "hooks"))

DISPATCH = 'python3 "$HOME/.claude/hooks/hook-dispatch.py"'
DISPATCH_INSTALLED = (
    'P="$HOME/.local/share/uv/python/cpython-3.14-macos-aarch64-none/bin/python3.14"; [ -x "$P" ] || P=python3; '
    'exec "$P" -I -S "$HOME/.claude/hooks/hook-dispatch.py"'
)

MERGE_GUARD = """import json, sys
data = json.load(sys.stdin)
if "gh pr merge" in (data.get("tool_input") or {}).get("command", ""):
    sys.stderr.write("BLOCKED: merge through the queue\\n")
    sys.exit(2)
"""

HOST_GUARD = """import json, sys
data = json.load(sys.stdin)
if "Herdr Shell" in (data.get("tool_input") or {}).get("command", ""):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "BLOCKED: not on the host"}}))
"""

QUIET = "import sys\nsys.stdin.read()\n"

CONTEXT = "import sys\nsys.stdin.read()\nprint('context from context-pulse')\n"


def processes_naming(text: str) -> list[tuple[int, str]]:

    rows = subprocess.run(["ps", "-axwwE", "-o", "pid=,stat=,command="], capture_output=True, text=True).stdout
    found = []
    for line in rows.splitlines():
        pid, state, rest = (line.split(None, 2) + ["", ""])[:3]
        if text in rest and pid.isdigit() and int(pid) != os.getpid():
            found.append((int(pid), state))
    return found


@pytest.fixture(autouse=True)
def kill_spawned_hooks(tmp_path: Path):

    yield
    for pid, _state in processes_naming(str(tmp_path)):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def make_home(tmp_path: Path) -> tuple[Path, dict]:
    home = tmp_path / "home"
    hooks = home / ".claude/hooks"
    hooks.mkdir(parents=True)
    for name, text in (
        ("no-direct-merge.py", MERGE_GUARD),
        ("herdr-shell-host-guard.py", HOST_GUARD),
        ("idle-agents.py", QUIET),
        ("context-pulse.py", CONTEXT),
        ("herdr-tab-autoname.py", QUIET),
    ):
        (hooks / name).write_text(text)
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "lane-bulletin-hook").write_text("#!/usr/bin/env python3\n" + QUIET)
    (bin_dir / "lane-bulletin-hook").chmod(0o755)
    py = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else "python3"
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": 'python3 "$HOME/.claude/hooks/no-direct-merge.py"'}],
                },
                {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "true", "timeout": 5}]},
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": f'{py} "$HOME/.claude/hooks/herdr-shell-host-guard.py"'}],
                },
                {"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "true"}]},
            ],
            "PostToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": '"$HOME/.local/bin/lane-bulletin-hook" 2>/dev/null || true',
                            "timeout": 3,
                        }
                    ]
                },
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'{py} "$HOME/.claude/hooks/context-pulse.py" 2>/dev/null || true',
                            "timeout": 3,
                            "statusMessage": "Routing pulse",
                        },
                        {
                            "type": "command",
                            "command": f'{py} "$HOME/.claude/hooks/herdr-tab-autoname.py" 2>/dev/null || true',
                            "timeout": 10,
                        },
                    ]
                },
            ],
            "Stop": [
                {
                    "hooks": [
                        {"type": "command", "command": f'{py} "$HOME/.claude/hooks/idle-agents.py" stop', "timeout": 10}
                    ]
                },
            ],
            "SessionStart": [{"hooks": [{"type": "command", "command": "true"}]}],
        }
    }
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    return home, settings


def installer(tmp_path: Path, home: Path):
    env = os.environ | {
        "HOME": str(home),
        "AGENT_LB_URL": "http://127.0.0.1:1",
        "ROUTE_MODELS_CACHE": str(tmp_path / "models-cache.json"),
        "LANG": "en_US.UTF-8",
    }
    for key in ("ROUTE_FIXTURE_DIR", "ROUTE_TABLE", "HOOK_DISPATCH_TRACE", "HOOK_DISPATCH_REGISTRY"):
        env.pop(key, None)

    def install(*options: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, str(SOURCE / "install-policy.py"), "--home", str(home), *options],
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    return install, env


def hooks_of(home: Path) -> dict:
    return json.loads((home / ".claude/settings.json").read_text())["hooks"]


def run_hook(command: str, payload: dict, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/sh", "-c", command], input=json.dumps(payload), env=env, capture_output=True, text=True, timeout=60
    )


MERGE_CALL = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5"}, "hook_event_name": "PreToolUse"}

MERGE_HOOK = {"type": "command", "command": 'python3 "$HOME/g/merge.py"'}

LINK_GUARD = ROOT / "tests/fixtures/hook-dispatch/link-cli-guard.sh"

LINK_HOOK = '"$HOME/.claude/hooks/link-cli-guard.sh"'

DISPATCH_BASH = f"{DISPATCH} PreToolUse 'Bash'"


def dispatcher_home(tmp_path: Path, entry: Any) -> tuple[Path, dict]:

    home = tmp_path / "home"
    (home / ".claude/hooks/dispatch").mkdir(parents=True)
    shutil.copy(SOURCE / "hooks/hook-dispatch.py", home / ".claude/hooks/hook-dispatch.py")
    for helper in (SOURCE / "hooks").glob("hook_*.py"):
        shutil.copy(helper, home / ".claude/hooks" / helper.name)
    shutil.copy(LINK_GUARD, home / ".claude/hooks/link-cli-guard.sh")
    (home / "g").mkdir()
    (home / "g/merge.py").write_text(MERGE_GUARD)
    registry = {"schema": 1, "entries": {"PreToolUse": {"Bash": entry}}, "inproc": []}
    (home / ".claude/hooks/dispatch/registry.json").write_text(json.dumps(registry))
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HOOK_DISPATCH_", "BASH_FUNC_")) and key not in ("BASH_ENV", "ENV", "BASHOPTS")
    }
    return home, env | {"HOME": str(home)}


def decision(result: subprocess.CompletedProcess) -> str:

    if result.returncode == 2:
        return "deny"
    try:
        obj = json.loads(result.stdout)
    except ValueError:
        return "allow"
    specific = obj.get("hookSpecificOutput") or {}
    return "deny" if specific.get("permissionDecision") == "deny" or obj.get("decision") == "block" else "allow"


def per_hook(hooks: list[dict], payload: dict, env: dict) -> str:
    return "deny" if any(decision(run_hook(hook["command"], payload, env)) == "deny" for hook in hooks) else "allow"


DENY_ALL = """import json, sys
sys.stdin.read()
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": "BLOCKED: G"}}))
"""

REWRITE = """import json, sys
sys.stdin.read()
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                         "updatedInput": {"command": %r}}}))
"""


def bash_groups(hooks: dict) -> list[dict]:

    return [
        group
        for group in hooks["PreToolUse"]
        if group.get("matcher") in (None, "", "*") or "Bash" in group["matcher"].split("|")
    ]


def as_result(command: str, done: subprocess.CompletedProcess) -> dict:
    return {"command": command, "code": done.returncode, "out": done.stdout, "err": done.stderr, "timed_out": False}


FAKE_RTK = """#!/bin/sh
input=$(cat)
case "$input" in
  *late-rewriter*) sleep 1.45 ;;
esac
printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"rewritten"}}}'
"""


def rewriter_home(tmp_path: Path, wrapper: str = "", timeout: float = 2.45) -> tuple[Path, dict]:
    home, env = dispatcher_home(tmp_path, [{"type": "command", "command": "rtk hook claude", "timeout": timeout}])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "rtk").write_text(wrapper or FAKE_RTK)
    (bin_dir / "rtk").chmod(0o755)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    return home, env | {"PATH": f"{bin_dir}:{env['PATH']}", "TMPDIR": str(tmp)}


TIMER_RTK = """#!/usr/bin/env python3
import json, signal, sys
sys.stdin.read()
left = signal.getitimer(signal.ITIMER_REAL)[0]  # a timer the dispatcher left running into this rewriter
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": "%.2f" % left}}}))
"""


def run_owned(command: str, payload: dict, env: dict, timeout: float) -> subprocess.CompletedProcess | None:

    proc = subprocess.Popen(
        ["/bin/sh", "-c", command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(json.dumps(payload), timeout=timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        return None
    finally:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        proc.wait(timeout=30)


QUIET_RTK = "#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\n"

DENY_MERGE = {"type": "command", "command": 'python3 "$HOME/g/merge.py"'}


def write_registry(home: Path, name: str, rev: str | None, entry: list) -> None:
    registry = {"schema": 1, "entries": {"PreToolUse": {"Bash": entry}}, "inproc": []}
    if rev:
        registry["rev"] = rev
    (home / ".claude/hooks/dispatch" / name).write_text(json.dumps(registry))


def rev_of(entry: list) -> str:

    entries = {"PreToolUse": {"Bash": entry}}
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()[:12]


PRETTIER = (
    'bash -c \'FILE=$(cat | jq -r ".tool_input.file_path // empty"); if [[ -n "$FILE" && "$FILE" =~ '
    '\\.(ts|tsx|js|jsx|json|css|md)$ ]] && (cd "$(dirname "$FILE")" && npx prettier --find-config-path '
    '"$FILE" >/dev/null 2>&1); then npx prettier --write "$FILE" 2>/dev/null; fi\''
)

FLAKY = "import sys, time\nsys.stdin.read()\nprint(time.time_ns())\n"


def policy_module():
    import importlib.util

    if str(SOURCE) not in sys.path:
        sys.path.insert(0, str(SOURCE))
    spec = importlib.util.spec_from_file_location("install_policy", SOURCE / "install-policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COUNTED = """import sys
sys.stdin.read()
with open(sys.argv[1], "a") as handle:
    handle.write("ran\\n")
"""


def rewrite_of(result: subprocess.CompletedProcess) -> Any:
    try:
        return ((json.loads(result.stdout) or {}).get("hookSpecificOutput") or {}).get("updatedInput")
    except ValueError:
        return None
