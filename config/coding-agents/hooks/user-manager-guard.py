#!/usr/bin/env python3

import json
import os
import re
import shlex
import sys

FLOOR = "floor: destructive: protected processes"
SEPARATORS = re.compile(r"\|\||&&|[;|&\n()]")
SIGNAL_TOOLS = {"kill", "pkill", "killall"}
END_TARGETS = {"exit", "halt", "poweroff", "reboot", "kexec", "isolate", "switch-root"}
END_UNITS = re.compile(r"^(?:exit|halt|poweroff|reboot|kexec)\.target$")
SESSION_ENDS = {"terminate-user", "kill-user", "terminate-session", "kill-session"}
PARENT_LOOKUP = re.compile(r"\bppid\b|/proc/[^/\s]+/stat\b|\bPPid\b|\bpstree\b")


def manager_pids():
    found = {1}
    try:
        names = os.listdir("/proc")
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as handle:
                argv = handle.read().split(b"\0")
        except OSError:
            continue
        if len(argv) > 1 and os.path.basename(argv[0]) == b"systemd" and b"--user" in argv[1:]:
            found.add(int(name))
    return found


def words_of(segment):
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        words = segment.split()
    while words and (
        re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])
        or words[0] in {"sudo", "env", "command", "exec", "nohup", "timeout", "nice", "then", "do", "!"}
    ):
        words = words[1:]
        if words and words[0].replace(".", "").isdigit():
            words = words[1:]
    return words


def signal_targets(args):
    targets, options, skip = [], True, False
    for index, arg in enumerate(args):
        if skip:
            skip = False
        elif options and arg == "--":
            options = False
        elif options and arg in {"-s", "-n"}:
            skip = True
        elif options and index == 0 and arg.startswith("-") and len(args) > 1:
            continue
        elif options and arg.startswith("-") and not re.fullmatch(r"-\d+", arg):
            continue
        else:
            targets.append(arg)
    return targets


def refusal(segment, protected):
    words = words_of(segment)
    if not words:
        return None
    tool, args = os.path.basename(words[0]), words[1:]
    if tool == "kill":
        for target in signal_targets(args):
            if target == "-1" or (target.lstrip("-").isdigit() and int(target.lstrip("-")) in protected):
                return f"kill names {target}, which is the user systemd manager, pid 1 or every process you own"
    if tool in {"pkill", "killall"}:
        joined = " ".join(args)
        if re.search(r"(?:^|\s)(?:-u|-U|--uid|--euid|--user)(?:\s|=|$)", joined):
            return f"{tool} by user ends every process you own, the user systemd manager included"
        if any("systemd" in a for a in args if not a.startswith("-")):
            return f"{tool} pattern matches systemd"
    if tool == "systemctl" and "--user" in args:
        verbs = [a for a in args if not a.startswith("-")]
        if verbs and verbs[0] in END_TARGETS:
            return f"systemctl --user {verbs[0]} stops every user service, T3 included"
        if verbs and verbs[0] in {"start", "isolate"} and any(END_UNITS.match(v) for v in verbs[1:]):
            return "starting an exit target stops every user service, T3 included"
    if tool == "loginctl" and any(a in SESSION_ENDS for a in args):
        return "loginctl ends the user's sessions and every service in them"
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(command, str) or not re.search(r"\b(?:kill|pkill|killall|systemctl|loginctl)\b", command):
        return 0
    if re.search(r"\bkill\b", command) and PARENT_LOOKUP.search(command):
        reason = "kill in the same command as a parent-PID lookup; an orphan's parent is the user systemd manager, so this ends T3 and every lane"
    else:
        protected = manager_pids()
        reason = None
        for segment in SEPARATORS.split(command):
            reason = refusal(segment.strip(), protected)
            if reason:
                break
    if not reason:
        return 0
    print(
        f"BLOCKED ({FLOOR}): {reason}. next: kill only the exact PIDs you started, or reap orphans with "
        "`systemctl --user start factory-worker-reap.service`; never signal a parent found through ps.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    code = main()
    if code == 0 and os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok user-manager-guard.py")
    sys.exit(code)
