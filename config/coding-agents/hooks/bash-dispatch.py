#!/usr/bin/env python3
"""PreToolUse(Bash) dispatcher: one hook process per Bash call in place of ~15 (2026-10-07).

Studio offload all-stop, 2026-10-07: every Bash call started 15 PreToolUse hooks, about 0.44 CPU-s and a third of
all new processes on the Studio across ~65 sessions. install-policy moves each plain Bash command hook (type,
command, timeout, statusMessage only) out of settings.json into LIST and registers this one hook in their place;
a hook with `args`, `if`, `async` or any other field stays registered directly. Uninstall puts them back.

Decisions stay identical. Each listed hook still runs as its own `/bin/sh -c` command with the same payload on stdin
and its own timeout (Claude Code's 600 s default when unset); a timed-out hook's process group is killed and counts
as no decision, as in Claude Code. This file skips a hook only when GATES says the hook's own fast path exits 0 with
no output for this payload, and only while the installed guard is byte-identical to the version the gate was copied
from (PINS, sha256); a changed or unreadable guard always runs. An unreadable payload runs every hook.

Results merge the way Claude Code merges parallel hooks (code.claude.com/docs/en/hooks): any exit 2 blocks with
every blocker's stderr; JSON stdout counts on every exit code when it validates; permissionDecision takes the
strongest of deny > defer > ask > allow (deprecated block/approve map to deny/allow); `continue: false` and its
stopReason survive; additionalContext and systemMessage from every hook are kept; one updatedInput is applied, by
PRIORITY when two hooks rewrite the same call (Claude Code applies one of parallel rewrites; box-offload first keeps
heavy checks off the Studio). A hook registered directly with the identical entry is skipped here so it never runs
twice. `bash-dispatch.py --pins` prints each gated guard and whether its pin still matches.
"""
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading

HOME = os.path.expanduser("~")
LIST = os.environ.get("BASH_DISPATCH_LIST", os.path.join(HOME, ".agent-lb/managed/coding-agents/bash-hooks.json"))
SETTINGS = os.environ.get("BASH_DISPATCH_SETTINGS", os.path.join(HOME, ".claude/settings.json"))
DEFAULT_TIMEOUT = 600.0  # Claude Code's default for a command hook
FOLDABLE = ("type", "command", "timeout", "statusMessage")  # install-policy folds only entries with these keys
# Rewriters, first wins when more than one returns updatedInput for the same call.
PRIORITY = ("box-offload-hook", "rm-cd-rewrite.py", "rtk hook claude")
DECISIONS = ("deny", "defer", "ask", "allow")  # strongest first
DESKTOP_TRIGGERS = ("cua", "osascript", "cliclick", "aerospace", "aside", "delivery_mode", "System Events", "keystroke",
                    "ails", "lectron", "screencapture")
INLINE_DANGEROUS = 'grep -qiE "rm\\s+-rf\\s+/|DROP'


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


# Command marker -> needed(raw, payload, cmd). False only when that guard's own first check exits 0 silently. The
# wide-scan guard has no such check (it refuses deep shell nesting with no trigger word), so it always runs.
GATES = (
    ("rm-dynamic-deny", lambda raw, p, cmd: "rm" in raw or "\\u" in raw),
    (INLINE_DANGEROUS, lambda raw, p, cmd: found(cmd, r"rm|drop", re.I)),
    ("agent-lb-bootout-guard.sh", lambda raw, p, cmd: has(cmd, "launchctl") and has(cmd, "agent-lb")),
    ("display-wake.sh", lambda raw, p, cmd: re.search(r"computer[_-]?use|cua-driver|screencapture",
                                                      str(p.get("tool_name") or p.get("tool") or "") + "\n" + cmd,
                                                      re.I) is not None),
    ("link-cli-guard.sh", lambda raw, p, cmd: has(cmd, "link-cli")),
    ("no-chrome-guard.sh", lambda raw, p, cmd: found(cmd, r"Chrom|remote-debugging-port|--headless|\.launch|"
                                                          r"launchPersistentContext")),
    ("no-direct-merge.py", lambda raw, p, cmd: has(cmd, "merge")),
    ("railway-vars-guard.sh", lambda raw, p, cmd: has(cmd, "railway")),
    # It reads command, cmd and arguments too, so look at the whole payload.
    ("herdr-shell-host-guard.py", lambda raw, p, cmd: "\\u" in raw or re.search(r"open|HerdrShell|app\.py|run\.sh",
                                                                                 raw) is not None),
    ("rm-cd-rewrite.py", lambda raw, p, cmd: has(cmd, "cd") and has(cmd, "rm")),
    ("desktop-guard", lambda raw, p, cmd: any(t in raw for t in DESKTOP_TRIGGERS)),
    ("stash-guard", lambda raw, p, cmd: "stash" in raw),
)
# sha256 of each guard the gate was read from (2026-10-07, Studio); the inline hook pins its command text. Refresh a
# pin only after rereading that guard's first check against its gate.
PINS = {
    "rm-dynamic-deny": "7a4cbabc6419bea6be1e1a48e1b5bfadcc93d64b22de6dc308dfcf9edc270e3c",
    INLINE_DANGEROUS: "0fbf6582d067534e0cf7179a1801b12c675c75397170a48d36aec72171483e7c",
    "agent-lb-bootout-guard.sh": "8f4a0eea5f7639e7d0f6e8d02a048ffd00db3c37daa9b6f4acb86d612a36d304",
    "display-wake.sh": "e701783e043516bdca4397ae0a1acdb8838645ee125931921e345704ce64056b",
    "link-cli-guard.sh": "cb71baf54200c8dfcd06ba98bb269cb954ed82d11a32ad63a0394d576482ef22",
    "no-chrome-guard.sh": "110606670fe66ed2f9dc5824e6d9cb4ed0819286d4b2f73f99b97a73196d9468",
    "no-direct-merge.py": "ad1689076bddf1f0f9734286f22c822c651b655dc5ca8d5334f548ce41de3f5f",
    "railway-vars-guard.sh": "9fa0e824554830e501bda0d9cb8a21213e6011935101b26f25e03a99796818dd",
    "herdr-shell-host-guard.py": "bfb5111afdf79d0078d0b62921ac3e71a944ccdb1cd0eac7abe399b48c422c93",
    "rm-cd-rewrite.py": "ce3cd4909f0c56e721284f23065fae6864c792b12ec756371e5ff4893c6727ba",
    "desktop-guard": "39bb14647d5bee4be86c457017f05ab32057230c5761213588d50aae194a9028",
    "stash-guard": "7525aab661d2d1cfad2013193dacb5f27b2d94a841fc3bf57242b7688543239d",
}


def guard_digest(command, marker):
    """sha256 of the file the hook command runs (the token naming the marker), or of the command when inline."""
    if marker == INLINE_DANGEROUS:
        return hashlib.sha256(command.encode()).hexdigest()
    token = re.search(r"""[^\s"']*""" + re.escape(marker) + r"""[^\s"']*""", command)
    if not token:
        return None
    path = os.path.expanduser(os.path.expandvars(token.group(0)))
    try:
        with open(path, "rb") as stream:
            return hashlib.sha256(stream.read()).hexdigest()
    except OSError:
        return None


def gate_for(command):
    for marker, gate in GATES:
        if marker in command:
            return gate if guard_digest(command, marker) == PINS.get(marker) else None
    return None


def needed(command, raw, payload):
    """True unless this hook's own fast path is known to exit 0 silently for this payload."""
    if payload is None:
        return True
    tool_input = payload.get("tool_input")
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str):
        return True
    gate = gate_for(command)
    if gate is None:
        return True
    try:
        return bool(gate(raw, payload, cmd))
    except Exception:
        return True


def valid_entry(hook):
    return (isinstance(hook, dict) and set(hook) <= set(FOLDABLE) and hook.get("type", "command") == "command"
            and isinstance(hook.get("command"), str) and hook["command"].strip() != ""
            and (hook.get("timeout") is None or (isinstance(hook["timeout"], (int, float))
                                                 and not isinstance(hook["timeout"], bool) and hook["timeout"] > 0))
            and (hook.get("statusMessage") is None or isinstance(hook["statusMessage"], str)))


def load_list():
    with open(LIST) as stream:
        hooks = json.load(stream)
    if not isinstance(hooks, list) or not hooks or not all(valid_entry(h) for h in hooks):
        raise ValueError("not a non-empty list of plain command hooks")
    return hooks


def direct_entries():
    """Unconditional Bash hook entries still registered directly in settings.json (they run there, not here)."""
    try:
        with open(SETTINGS) as stream:
            groups = json.load(stream).get("hooks", {}).get("PreToolUse", [])
        return [hook for group in groups if group.get("matcher") == "Bash" for hook in group.get("hooks", [])
                if valid_entry(hook)]
    except Exception:
        return []


def run(hook, raw, results, index):
    timeout = float(hook.get("timeout") or DEFAULT_TIMEOUT)
    try:
        proc = subprocess.Popen(["/bin/sh", "-c", hook["command"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except Exception as exc:
        results[index] = (1, "", f"bash-dispatch: {hook['command'][:60]}: {exc}\n")
        return
    try:
        out, err = proc.communicate(raw, timeout=timeout)
        results[index] = (proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # the whole group: a guard's children die with it
        except OSError:
            pass
        proc.communicate()
        results[index] = None  # Claude Code: a timed-out hook is a non-blocking error, no decision


def decision(stdout):
    """The hook's JSON output when Claude Code would parse and accept it, else None."""
    text = stdout.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    specific = data.get("hookSpecificOutput", {})
    checks = (
        isinstance(specific, dict),
        not isinstance(specific, dict) or specific.get("permissionDecision") in (None,) + DECISIONS,
        not isinstance(specific, dict) or specific.get("updatedInput") is None
        or isinstance(specific.get("updatedInput"), dict),
        not isinstance(specific, dict) or specific.get("additionalContext") is None
        or isinstance(specific.get("additionalContext"), str),
        data.get("continue") is None or isinstance(data.get("continue"), bool),
        data.get("decision") in (None, "approve", "block"),
        all(data.get(k) is None or isinstance(data.get(k), str) for k in ("stopReason", "systemMessage", "reason")),
    )
    return data if all(checks) else None


def rank(command):
    return next((i for i, marker in enumerate(PRIORITY) if marker in command), len(PRIORITY))


def merge(hooks, results):
    """(exit code, stdout, stderr) for Claude Code from each hook's own (exit code, stdout, stderr)."""
    rows = [(hook, result) for hook, result in zip(hooks, results) if result is not None]
    blocked = [err for _, (code, _, err) in rows if code == 2]
    outputs, errors = [], []
    for hook, (code, out, err) in rows:
        data = decision(out) if out.strip() else None
        if data is not None:
            outputs.append((hook, data))
        elif code not in (0, 2) or out.strip().startswith("{"):
            errors.append(err or out)
    merged = {}
    stops = [data for _, data in outputs if data.get("continue") is False]
    if stops:
        merged["continue"] = False
        reasons = [data["stopReason"] for data in stops if data.get("stopReason")]
        if reasons:
            merged["stopReason"] = "\n".join(reasons)
    messages = [data["systemMessage"] for _, data in outputs if data.get("systemMessage")]
    if messages:
        merged["systemMessage"] = "\n".join(messages)
    specific = {}
    contexts = [data["hookSpecificOutput"]["additionalContext"] for _, data in outputs
                if (data.get("hookSpecificOutput") or {}).get("additionalContext")]
    if contexts:
        specific["additionalContext"] = "\n\n".join(contexts)
    if blocked:
        # Exit 2 blocks with its stderr whatever the JSON says; the universal fields still apply.
        if specific:
            merged["hookSpecificOutput"] = {"hookEventName": "PreToolUse", **specific}
        stderr = "".join(e if e.endswith("\n") or not e else e + "\n" for e in blocked)
        return 2, json.dumps(merged) if merged else "", stderr
    verdicts = []
    for hook, data in outputs:
        own = data.get("hookSpecificOutput") or {}
        choice = own.get("permissionDecision") or {"block": "deny", "approve": "allow"}.get(data.get("decision"))
        if choice:
            reason = own.get("permissionDecisionReason") or data.get("reason")
            verdicts.append((DECISIONS.index(choice), choice, reason))
    if verdicts:
        strongest = min(v[0] for v in verdicts)
        choice = DECISIONS[strongest]
        specific["permissionDecision"] = choice
        reasons = [reason for index, _, reason in verdicts if index == strongest and reason]
        if reasons:
            specific["permissionDecisionReason"] = "\n".join(reasons)
    rewrites = [(hook, data["hookSpecificOutput"]["updatedInput"]) for hook, data in outputs
                if isinstance((data.get("hookSpecificOutput") or {}).get("updatedInput"), dict)]
    if rewrites:
        specific["updatedInput"] = min(rewrites, key=lambda pair: rank(pair[0]["command"]))[1]
    if specific:
        merged["hookSpecificOutput"] = {"hookEventName": "PreToolUse", **specific}
    if merged:
        return 0, json.dumps(merged), "".join(errors)
    if errors:
        return 1, "", errors[0]
    plain = "".join(out for _, (code, out, _) in rows if code == 0)
    return 0, plain, ""


def pins():
    groups = json.load(open(SETTINGS)).get("hooks", {}).get("PreToolUse", [])
    try:
        listed = load_list()
    except Exception:
        listed = []
    commands = [h["command"] for h in listed] + [h.get("command", "") for g in groups
                                                       if g.get("matcher") == "Bash" for h in g.get("hooks", [])]
    for marker, _ in GATES:
        command = next((c for c in commands if marker in c), None)
        state = "absent" if command is None else (
            "gated" if guard_digest(command, marker) == PINS[marker] else "stale pin, runs every call")
        print(f"{marker[:40]:40} {state}")
    return 0


def main():
    if sys.argv[1:] == ["--pins"]:
        return pins()
    raw = sys.stdin.read()
    try:
        hooks = load_list()
    except Exception as exc:
        # Fail closed: without a valid list no guard would run (S-01). install-policy writes it with the dispatcher.
        sys.stderr.write(f"bash-dispatch: cannot read the Bash hook list {LIST} ({type(exc).__name__}: {exc}); every "
                         "Bash call is refused until `install-policy.py` rewrites it or `install-policy.py "
                         "--uninstall` restores the hooks.\n")
        return 2
    try:
        payload = json.loads(raw)
        payload = payload if isinstance(payload, dict) else None
    except ValueError:
        payload = None
    direct = direct_entries()
    hooks = [h for h in hooks if h not in direct and needed(h["command"], raw, payload)]
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
