import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from no_chrome_classify import classify  # type: ignore[missing-import]
from no_chrome_shell import Doubt  # type: ignore[missing-import]


def allow():
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok no-chrome-guard.sh")
    return 0


def refuse(reason):
    print(
        "BLOCKED (floor: the owner's own browser and accounts): no-chrome-guard "
        + reason
        + ", so the call is refused (the guard fails closed).",
        file=sys.stderr,
    )
    return 2


def approved(command):
    match = re.search(r"#[ \t]*(?:owner|alex)-approval:[ \t]*([^\s]+)[ \t]*$", command.splitlines()[-1])
    if not match:
        return False
    ref = match[1]
    if ref.startswith("~/"):
        ref = os.path.join(os.environ["HOME"], ref[2:])
    elif ref.startswith("$HOME/"):
        ref = os.path.join(os.environ["HOME"], ref[6:])
    directory = os.path.join(os.environ["HOME"], ".open-factory/approvals")
    if ".." in ref or os.path.dirname(ref) != directory:
        return False
    if not re.fullmatch(r"(?:owner|alex)-.*-2026-.*\.md", os.path.basename(ref)):
        return False
    try:
        return bool(re.search(r"[0-9]{1,2}:[0-9]{2}", Path(ref).read_text()))
    except (OSError, UnicodeError):
        return False


def ticket_reason(ask_id, command, pc_hit, payload):
    home = os.environ.get("FACTORY_HOME") or os.path.join(os.environ["HOME"], ".agent-rails/factory")
    log_path = os.path.join(home, "state/no-chrome-guard.jsonl")
    action = re.sub(r"(^|(?<=\s))#[ \t]*unblock-ask:[ \t]*" + re.escape(ask_id) + r"[ \t]*$", "", command).strip()

    def verdict():
        try:
            out = subprocess.run(
                [os.path.join(os.environ["HOME"], ".local/bin/unblock"), "show", ask_id, "--json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            ask = json.loads(out.stdout) if out.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            ask = None
        if not isinstance(ask, dict):
            return "ask %s is unreadable" % ask_id
        permission = ask.get("permission") if isinstance(ask.get("permission"), dict) else {}
        via = ask.get("answered_via")
        if ask.get("ticket") != ask_id or ask.get("purpose") != "permission" or permission.get("tool") != "Bash":
            return "ask %s is not a Bash permission ask" % ask_id
        if (
            ask.get("status") not in ("answered", "collected", "orphaned")
            or (ask.get("answers") or {}).get("verdict") != "allow_once"
        ):
            return "ask %s is not approved (Allow once)" % ask_id
        if not isinstance(via, str) or not via or via in ("local", "share-link:local"):
            return "ask %s was not answered by the owner on the page" % ask_id
        if str(permission.get("command") or "").strip() != action:
            return "this call differs from the command ask %s approved" % ask_id
        if pc_hit:
            try:
                with open(os.path.join(home, "state/pc-game-signal.json")) as handle:
                    mode = json.load(handle).get("mode")
            except (OSError, ValueError, AttributeError):
                mode = None
            if mode != "normal":
                return "the PC is in game mode (or its game signal is unreadable); nothing touches it now"
        return None

    reason = verdict()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a+") as log:
        fcntl.flock(log, fcntl.LOCK_EX)
        if reason is None:
            log.seek(0)
            for line in log:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("ask") == ask_id and row.get("decision") == "allow":
                    reason = "ask %s was already used at %s" % (ask_id, row.get("at"))
                    break
        log.seek(0, os.SEEK_END)
        log.write(
            json.dumps(
                {
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "ask": ask_id,
                    "decision": "deny" if reason else "allow",
                    "reason": reason,
                    "action": action,
                    "pane": os.environ.get("HERDR_PANE_ID"),
                    "session": payload.get("session_id"),
                }
            )
            + "\n"
        )
    return reason


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("object required")
        tool = payload.get("tool_input")
        if tool is not None and not isinstance(tool, dict):
            raise ValueError("tool_input object required")
        command = (tool or {}).get("command")
        if command is None:
            command = ""
        if not isinstance(command, str) or "\0" in command:
            raise ValueError("command string required")
    except (OSError, ValueError):
        return refuse("could not read the hook input")
    if not command:
        return allow()
    try:
        kind = classify(command)
    except (Doubt, ValueError, OSError):
        kind = "browser"
    if kind in {"none", "pc"} or approved(command):
        return allow()
    match = re.search(r"(?:^|\s)#[ \t]*unblock-ask:[ \t]*(ub_[A-Za-z0-9]+)[ \t]*$", command)
    if not match:
        return refuse("requires an exact-command Allow once ticket or a timestamped owner-approval record")
    try:
        reason = ticket_reason(match[1], command, kind in {"browser", "pc", "macpc"}, payload)
    except (OSError, ValueError, TypeError):
        return refuse("the one-shot check failed")
    return refuse(reason) if reason else allow()


if __name__ == "__main__":
    sys.exit(main())
