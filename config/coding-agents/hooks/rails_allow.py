import json
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

import yes_words  # type: ignore[missing-import]

CLI_HOME = ".agent-rails/agents/factory/.rails-one"
CLI_HOST = "ax102"
OWNER = "owner"
STAMP = "%Y-%m-%dT%H:%M:%S.%f%z"
PRESSED = {"click", "batch_click", "list_click", "chat_yes", "said", "door", "pin"}
SETTLED = {"allowed", "consumed"}
EVIDENCE = re.compile(
    r"^[\s>*_-]*Evidence\s*:\s*[`*_]*\s*rails\s+(ask|hold)\s+`?([A-Za-z0-9_]{6,64})\b",
    re.MULTILINE | re.IGNORECASE,
)


class Refused(Exception):
    pass


def cited(text):
    found = EVIDENCE.findall(text)
    if not found:
        return None
    if len(found) != 1:
        raise Refused(
            "it cites more than one Rails ask or hold; one record holds one Allow"
        )
    kind, ident = found[0]
    return kind.lower(), ident


def call(tool, args, seconds):
    payload = json.dumps(args)
    local = Path.home() / CLI_HOME
    if (local / "rails").is_file():
        command = [str(local / "rails"), tool, payload]
        env = dict(os.environ, RAILS_HOME=str(local))
    else:
        remote = f"RAILS_HOME=$HOME/{CLI_HOME} $HOME/{CLI_HOME}/rails {tool} {shlex.quote(payload)}"
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=2",
            CLI_HOST,
            remote,
        ]
        env = None
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=seconds,
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        raise Refused(
            f"Factory's Rails {tool} read did not answer in {seconds} s"
        ) from None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        data = None
    if result.returncode or not isinstance(data, dict):
        said = (result.stderr or result.stdout).strip().splitlines()
        raise Refused(
            f"Factory's Rails {tool} read failed ({said[0][:160] if said else 'no output'}), so no Allow can be confirmed"
        )
    return data


def asked(ident, seconds):
    ask = call("inbox", {"ask_id": ident}, seconds).get("ask") or {}
    answer = ask.get("answer") or {}
    if ask.get("id") != ident:
        raise Refused(f"Factory has no Rails ask {ident}")
    if ask.get("delegate") != OWNER:
        raise Refused(f"ask {ident} was not addressed to Alex")
    if answer.get("answered_by") != OWNER:
        raise Refused(
            f"ask {ident} was answered by {answer.get('answered_by_name') or answer.get('answered_by') or 'nobody'}, not by Alex himself"
        )
    if answer.get("source") is not None:
        raise Refused(
            f"ask {ident} was answered through {answer.get('source')}, not by Alex himself in Inbox"
        )
    if answer.get("not_needed") or ask.get("dropped_at"):
        raise Refused(f"ask {ident} was closed as not needed")
    choice = answer.get("choice")
    typed = answer.get("text")
    if isinstance(choice, str) and choice in (ask.get("options") or []):
        said, pressed, yes = choice, True, yes_words.pressed_yes(choice)
    elif not choice and isinstance(typed, str):
        said, pressed, yes = typed, False, not yes_words.refused(typed)
    else:
        raise Refused(
            f"Alex's answer on ask {ident} is neither one of its options nor typed by him"
        )
    if not yes:
        raise Refused(
            f"Alex's answer on ask {ident} is not a yes (a pressed option opening with Yes or Allow and holding no "
            "but, only, nothing or no, or typed words with no no, not, wait, stop, never or question mark)"
        )
    asked_words = "\n".join(str(ask.get(key) or "") for key in ("title", "text"))
    return {"said": said, "asked": asked_words, "pressed": pressed}, answer.get(
        "answered_at"
    )


def held(ident, seconds):
    holds = call("hold_list", {"limit": 100}, seconds).get("holds") or []
    hold = next(
        (row for row in holds if isinstance(row, dict) and row.get("hold_id") == ident),
        None,
    )
    if hold is None:
        raise Refused(f"hold {ident} is not among Factory's latest 100 holds")
    if hold.get("status") not in SETTLED:
        raise Refused(f"hold {ident} is {hold.get('status')}, not allowed")
    if hold.get("is_owner") is not True or hold.get("allowed_by") not in PRESSED:
        raise Refused(
            f"hold {ident} was not allowed by Alex's own press (allowed_by {hold.get('allowed_by')})"
        )
    text = hold.get("text")
    if not isinstance(text, str):
        raise Refused(f"hold {ident} carries no text to read")
    return {"said": text, "asked": text, "pressed": True}, hold.get("answered_at")


def allow(text, seconds):
    kind, ident = cited(text)
    answer, at = (asked if kind == "ask" else held)(ident, seconds)
    try:
        stamp = datetime.strptime(str(at), STAMP)
    except ValueError:
        raise Refused(f"Rails {kind} {ident} carries no answer time") from None
    if stamp > datetime.now(stamp.tzinfo):
        raise Refused(f"Rails {kind} {ident} is answered in the future")
    return answer, stamp, f"rails {kind} {ident}"
