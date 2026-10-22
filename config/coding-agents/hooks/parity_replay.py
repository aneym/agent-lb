from concurrent.futures import ThreadPoolExecutor
import os
import re
import shutil
from parity_comparison import run_case, timing_only
from parity_execution import floor_of, hook_timeout, legacy
from parity_registry import build_registry, group_key
from parity_sandbox import OVERLAY, Sandbox


SYNTHETIC = {
    "crash.py": ("import sys\nraise RuntimeError('synthetic crash')\n", 0o755),
    "hang.py": ("import time\ntime.sleep(30)\n", 0o755),
    "failclosed.py": (
        "import sys\ntry:\n    raise ValueError('bad input')\nexcept Exception as e:\n"
        "    sys.stderr.write('synthetic guard error (%s); refused\\n' % type(e).__name__)\n"
        "    sys.exit(2)\n",
        0o755,
    ),
    "crash.sh": ("#!/bin/bash\necho 'synthetic shell crash' >&2\nexit 1\n", 0o755),
    "hang.sh": ("#!/bin/bash\nsleep 30\n", 0o755),
    "failclosed.sh": ("#!/bin/bash\necho 'BLOCKED: synthetic shell refusal' >&2\nexit 2\n", 0o755),
}



FAKE_RTK = """#!/bin/sh
input=$(cat)
case "$input" in
  *late-rewriter*) sleep 1.45 ;;
esac
printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"rtk rewritten"}}}'
"""



def synthetic_registry():
    prefix = "$HOME/synth/"
    hooks = [
        {"type": "command", "command": '/usr/bin/python3 "%scrash.py"' % prefix},
        {"type": "command", "command": 'python3 "%shang.py"' % prefix, "timeout": 2},
        {"type": "command", "command": '"%scrash.sh"' % prefix},
        {"type": "command", "command": '"%shang.sh"' % prefix, "timeout": 2},
        {"type": "command", "command": '/usr/bin/python3 "%scrash.py" 2>/dev/null || true' % prefix},
        {
            "type": "command",
            "command": '/usr/bin/python3 "%scrash.py" 2>/dev/null || { printf %%s '
            '\'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
            '"additionalContext":"synthetic fallback"}}\'; }' % prefix,
        },
    ]
    closed = [
        {"type": "command", "command": '/usr/bin/python3 "%sfailclosed.py"' % prefix},
        {"type": "command", "command": '"%sfailclosed.sh"' % prefix},
        {"type": "command", "command": 'python3 "%shang.py"' % prefix, "timeout": 2},
    ]
    groups = [
        {"matcher": "Bash", "hooks": hooks},
        {"matcher": "Agent", "hooks": closed},
        {"matcher": "RewriteSlow", "hooks": [{"type": "command", "command": "rtk hook claude", "timeout": 2.45}]},
        {"matcher": "RewriteLate", "hooks": [{"type": "command", "command": "rtk hook claude", "timeout": 1}]},
    ]
    return build_registry({"PreToolUse": groups}, ["crash.py", "hang.py", "failclosed.py"])



FLOOR_MODES = ("timeout", "crash", "missing", "malformed", "no-receipt")



PY_STUBS = {
    "timeout": "import time\ntime.sleep(30)\n",
    "crash": "raise RuntimeError('synthetic floor crash')\n",
    "no-receipt": "import sys\nsys.stdin.read()\n",
    "malformed": "import sys\nsys.stdin.read()\nprint('not a hook answer {')\n",
}



SH_STUBS = {
    "timeout": "#!/bin/bash\nexec sleep 30\n",
    "crash": "#!/bin/bash\necho 'synthetic floor crash' >&2\nexit 1\n",
    "missing": '#!/bin/bash\necho "bash: $0: No such file or directory" >&2\nexit 127\n',
    "malformed": "#!/bin/bash\ncat >/dev/null\necho 'not a hook answer {'\n",
    "no-receipt": "#!/bin/bash\ncat >/dev/null\nexit 0\n",
}



REPLAY_INPUT = {
    "Bash": {"command": "echo launchctl railway link-cli; rm -rf /tmp/hook-dispatch-floor-replay"},
    "Agent": {"subagent_type": "Explore", "prompt": "x", "description": "x"},
    "Workflow": {"script": "agent('x', {agentType: 'Explore'})"},
}



def replay_leaves(registry):
    leaves, control = [], None
    for group in registry["per_hook"].get("PreToolUse", []):
        key = group_key(group)
        for hook in group.get("hooks", []):
            name = floor_of(hook.get("command", ""))
            if name and not any(other["command"] == hook["command"] for _n, _k, other in leaves):
                leaves.append((name, key, hook))
            elif control is None and not name and key == "Bash" and SHAPE_SCRIPT.match(hook.get("command", "")):
                control = (None, key, hook)
    return leaves + ([control] if control else [])



SHAPE_SCRIPT = re.compile(r'^\s*(?:(/usr/bin/python3|python3|/bin/bash|bash)\s+)?("\$HOME/[^"]+"|\$HOME/\S+|~/\S+)')



def replay_case(root, dispatcher, real_home, inproc, index, name, key, hook, mode):
    command = hook["command"]
    match = SHAPE_SCRIPT.match(legacy(command) or command)
    extra, remove = {}, []
    if match:
        rel = match.group(2).strip('"').replace("$HOME/", "", 1).replace("~/", "", 1)
        python = "python" in (match.group(1) or "") or (
            not match.group(1)
            and (interpreter_line(OVERLAY.get(rel) or os.path.join(real_home, rel)) or "").find("python") >= 0
        )
        if mode == "missing":
            remove.append(os.path.join("home", rel))
        else:
            extra[os.path.join("home", rel)] = (
                (("#!/usr/bin/env python3\n" + PY_STUBS[mode]) if python else SH_STUBS[mode]),
                0o755,
            )
    else:
        extra[os.path.join("bin", "bash")] = (SH_STUBS[mode], 0o755)
    timed = dict(hook, timeout=1) if mode == "timeout" else dict(hook, timeout=min(hook_timeout(hook), 20))
    registry = build_registry({"PreToolUse": [{"matcher": key, "hooks": [timed]}]}, inproc)
    sandbox = Sandbox(
        os.path.join(root, "floor-%03d" % index), real_home, registry, dispatcher, [command], extra, remove
    )
    tool = key.split("|")[0] if key != "*" else "Bash"
    case = dict(
        event="PreToolUse",
        tool=tool,
        input=REPLAY_INPUT.get(tool, {"command": "ls"}),
        expect=None,
        name="floor-%s-%s" % (name or "control", mode),
        replay=True,
    )
    outcome = run_case(sandbox, registry, case)
    shutil.rmtree(sandbox.root, ignore_errors=True)
    want = "deny" if name else outcome["decision"]
    return {
        "name": case["name"],
        "leaf": name,
        "control": name is None,
        "mode": mode,
        "command": command[:120],
        "per_hook_decision": outcome["decision"],
        "decision": outcome["new_decision"],
        "message": outcome["new_block_message"][:300],
        "parity": outcome["result"],
        "mismatches": outcome["mismatches"],
        "ok": outcome["new_decision"] == want and outcome["result"] == "pass",
    }



def interpreter_line(path):
    try:
        with open(path, "rb") as handle:
            first = handle.readline(200).decode("utf-8", "replace")
    except OSError:
        return None
    return first if first.startswith("#!") else None



def floor_replay(root, dispatcher, real_home, registry, jobs=4):
    inproc = list(registry.get("inproc") or [])
    jobs_list = [
        (index, name, key, hook, mode)
        for index, (name, key, hook) in enumerate(replay_leaves(registry))
        for mode in FLOOR_MODES
    ]
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(
            pool.map(
                lambda job: replay_case(
                    root, dispatcher, real_home, inproc, job[0] * 10 + FLOOR_MODES.index(job[4]), *job[1:]
                ),
                jobs_list,
            )
        )



def crash_cases(root, dispatcher, real_home):
    registry = synthetic_registry()
    extra = {os.path.join("home", "synth", name): spec for name, spec in SYNTHETIC.items()}
    extra[os.path.join("bin", "rtk")] = (FAKE_RTK, 0o755)
    sandbox = Sandbox(os.path.join(root, "synthetic"), real_home, registry, dispatcher, [], extra)
    out = []
    for name, tool, command in (
        ("crash-and-timeout-fail-open", "Bash", "true"),
        ("crash-fail-closed-and-timeout", "Agent", "true"),
        ("rewriter-execd-rewrites", "RewriteSlow", "echo plain-rewriter"),
        ("rewriter-late-times-out", "RewriteLate", "echo late-rewriter"),
    ):
        case = dict(event="PreToolUse", tool=tool, input={"command": command, "prompt": "x"}, name=name, expect=None)
        outcome, attempts = run_case(sandbox, registry, case), 1
        while timing_only(outcome) and attempts < 3:
            attempts += 1
            outcome = dict(run_case(sandbox, registry, case), attempts=attempts)
        out.append(outcome)
    return out

