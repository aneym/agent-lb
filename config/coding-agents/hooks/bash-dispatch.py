#!/usr/bin/env python3
"""PreToolUse(Bash) dispatcher: one hook process per Bash call in place of ~15 (2026-10-07).

Studio offload all-stop, 2026-10-07: every Bash call started 15 PreToolUse hooks, about 0.44 CPU-s and a third of
all new processes on the Studio across ~65 sessions. install-policy moves each Bash hook out of settings.json into
LIST (same command, same timeout, same order) and registers this one hook in their place; uninstall puts them back.

Decisions stay identical. Each listed hook still runs as its own command with the same payload on stdin; this file
only skips a hook when the hook's own fast path would exit 0 with no output for this payload (GATES, each copied
from the guard's first lines and pinned by test_bash_dispatch.py), and runs every hook when the payload cannot be
read. Results merge the way Claude Code merges parallel hooks: any exit 2 blocks with the joined stderr; else a
JSON deny wins; else one updatedInput is applied, by PRIORITY when two hooks rewrite the same call (Claude Code
picks one of parallel rewrites; box-offload first keeps heavy checks off the Studio). A hook that is still also
registered directly in settings.json is skipped here so it never runs twice.
"""
import json
import os
import re
import subprocess
import sys
import threading

HOME = os.path.expanduser("~")
LIST = os.environ.get("BASH_DISPATCH_LIST", os.path.join(HOME, ".agent-lb/managed/coding-agents/bash-hooks.json"))
SETTINGS = os.environ.get("BASH_DISPATCH_SETTINGS", os.path.join(HOME, ".claude/settings.json"))
DEFAULT_TIMEOUT = 60.0
# Rewriters, first wins when more than one returns updatedInput for the same call.
PRIORITY = ("box-offload-hook", "rm-cd-rewrite.py", "rtk hook claude")
DESKTOP_TRIGGERS = ("cua", "osascript", "cliclick", "aerospace", "aside", "delivery_mode", "System Events", "keystroke",
                    "ails", "lectron", "screencapture")


def spellings(cmd):
    """The command and the command with quotes, backslashes, $ and line continuations removed: a shell-reading guard
    sees me"rge" or g\\rep as the plain word."""
    return (cmd, cmd.replace("\\\n", "").translate(str.maketrans("", "", "'$\"\\")))


def has(cmd, *words):
    if "$'" in cmd and "\\" in cmd:
        return True  # an ANSI-C string can spell any word; run the guard
    return any(word in text for text in spellings(cmd) for word in words)


def found(cmd, pattern, flags=0):
    if "$'" in cmd and "\\" in cmd:
        return True
    return any(re.search(pattern, text, flags) for text in spellings(cmd))


# Command marker -> needed(raw, payload, cmd). False only when that guard's own first check exits 0 silently.
GATES = (
    ("rm-dynamic-deny", lambda raw, p, cmd: "rm" in raw or "\\u" in raw),
    ('grep -qiE "rm\\s+-rf\\s+/|DROP', lambda raw, p, cmd: found(cmd, r"rm|drop", re.I)),
    ("agent-lb-bootout-guard.sh", lambda raw, p, cmd: has(cmd, "launchctl") and has(cmd, "agent-lb")),
    ("display-wake.sh", lambda raw, p, cmd: re.search(r"computer[_-]?use|cua-driver|screencapture",
                                                      str(p.get("tool_name") or p.get("tool") or "") + "\n" + cmd,
                                                      re.I) is not None),
    ("wide-scan-guard.sh", lambda raw, p, cmd: has(cmd, "grep", "rg", "find", "fd")),
    ("link-cli-guard.sh", lambda raw, p, cmd: has(cmd, "link-cli")),
    ("no-chrome-guard.sh", lambda raw, p, cmd: found(cmd, r"Chrom|remote-debugging-port|--headless|\.launch|"
                                                          r"launchPersistentContext")),
    ("no-direct-merge.py", lambda raw, p, cmd: has(cmd, "merge")),
    ("railway-vars-guard.sh", lambda raw, p, cmd: has(cmd, "railway")),
    ("herdr-shell-host-guard.py", lambda raw, p, cmd: found(cmd, r"open|HerdrShell|app\.py|run\.sh")),
    ("rm-cd-rewrite.py", lambda raw, p, cmd: has(cmd, "cd") and has(cmd, "rm")),
    ("desktop-guard", lambda raw, p, cmd: any(t in raw for t in DESKTOP_TRIGGERS)),
    ("stash-guard", lambda raw, p, cmd: "stash" in raw),
)


def gate_for(command):
    return next((gate for marker, gate in GATES if marker in command), None)


def needed(command, raw, payload):
    """True unless this hook's own fast path is known to exit 0 silently for this payload."""
    gate = gate_for(command)
    if gate is None or payload is None:
        return True
    tool_input = payload.get("tool_input")
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str):
        return True
    try:
        return bool(gate(raw, payload, cmd))
    except Exception:
        return True


def direct_commands():
    """Bash hook commands still registered directly in settings.json (they run there, not here)."""
    try:
        with open(SETTINGS) as stream:
            groups = json.load(stream).get("hooks", {}).get("PreToolUse", [])
        return {hook.get("command") for group in groups if group.get("matcher") == "Bash"
                for hook in group.get("hooks", [])}
    except Exception:
        return set()


def run(hook, raw, results, index):
    timeout = hook.get("timeout") or DEFAULT_TIMEOUT
    try:
        done = subprocess.run(["/bin/sh", "-c", hook["command"]], input=raw, capture_output=True, text=True,
                              timeout=float(timeout))
        results[index] = (done.returncode, done.stdout, done.stderr)
    except subprocess.TimeoutExpired:
        results[index] = None  # Claude Code: a timed-out hook is a non-blocking error
    except Exception as exc:
        results[index] = (1, "", f"bash-dispatch: {hook.get('command', '')[:60]}: {exc}\n")


def decision(stdout):
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def merge(hooks, results):
    """(exit code, stdout, stderr) for Claude Code from each hook's own (exit code, stdout, stderr)."""
    rows = [(hook, result) for hook, result in zip(hooks, results) if result is not None]
    blocked = [err for _, (code, _, err) in rows if code == 2]
    if blocked:
        return 2, "", "".join(e if e.endswith("\n") or not e else e + "\n" for e in blocked)
    outputs = [(hook, decision(out)) for hook, (code, out, _) in rows if code == 0 and out.strip()]
    outputs = [(hook, data) for hook, data in outputs if data is not None]
    for _, data in outputs:
        specific = data.get("hookSpecificOutput") or {}
        if specific.get("permissionDecision") == "deny" or data.get("decision") == "block":
            return 0, json.dumps(data), ""
    rewrites = [(hook, data) for hook, data in outputs
                if isinstance((data.get("hookSpecificOutput") or {}).get("updatedInput"), dict)]
    if rewrites:
        rank = lambda pair: next((i for i, m in enumerate(PRIORITY) if m in pair[0]["command"]), len(PRIORITY))
        return 0, json.dumps(min(rewrites, key=rank)[1]), ""
    if outputs:
        return 0, json.dumps(outputs[0][1]), ""
    errors = [(code, err) for _, (code, _, err) in rows if code not in (0, 2) and err]
    if errors:
        return 1, "", errors[0][1]
    plain = "".join(out for _, (code, out, _) in rows if code == 0)
    return 0, plain, ""


def main():
    raw = sys.stdin.read()
    try:
        with open(LIST) as stream:
            hooks = [h for h in json.load(stream) if isinstance(h, dict) and isinstance(h.get("command"), str)]
    except Exception as exc:
        # Fail closed: without the list no guard would run (S-01). install-policy writes it with the dispatcher.
        sys.stderr.write(f"bash-dispatch: cannot read the Bash hook list {LIST} ({type(exc).__name__}); every Bash call "
                         "is refused until `install-policy.py` rewrites it or `install-policy.py --uninstall` restores "
                         "the hooks.\n")
        return 2
    try:
        payload = json.loads(raw)
        payload = payload if isinstance(payload, dict) else None
    except ValueError:
        payload = None
    direct = direct_commands()
    hooks = [h for h in hooks if h["command"] not in direct and needed(h["command"], raw, payload)]
    results = [None] * len(hooks)
    threads = [threading.Thread(target=run, args=(h, raw, results, i)) for i, h in enumerate(hooks)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    code, out, err = merge(hooks, results)
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
