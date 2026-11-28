#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import approval_ask  # type: ignore[missing-import]
import approval_scope  # type: ignore[missing-import]
import t3_yes  # type: ignore[missing-import]
import yes_words  # type: ignore[missing-import]

HERE = Path(__file__).resolve().parent
GRANDFATHERED = HERE / "approval_grandfathered.txt"
ROOTS = (
    ".agent-rails/approvals",
    ".open-factory/approvals",
    ".agent-rails/lanes/orchestrator-refs",
)
LEGACY = {
    ".agent-rails/approvals": {"delete", "stash", "browser", "provider", "data-delete"},
    ".agent-rails/lanes/orchestrator-refs": {"destructive"},
    ".open-factory/approvals": set(),
}
IRREVERSIBLE = {"destructive", "provider", "data-delete", "delete"}
FLOORS = IRREVERSIBLE | {"browser", "stash", "desktop"}
DELETES = {"delete", "data-delete", "destructive"}
NAME = re.compile(r"(?:owner|alex)-[A-Za-z0-9._-]+\.md")
UUID = r"(?:mcp:)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
EVIDENCE = re.compile(
    r"^[\s>*_-]*Evidence\s*:\s*[`*_]*\s*t3\s+`?" + UUID + r"\b",
    re.MULTILINE | re.IGNORECASE,
)
COVERS = re.compile(r"^[\s>*_-]*Covers\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
LIFE = timedelta(hours=24)
FORMAT = (
    "a record cites Evidence: t3 <id of the message Alex typed> and Covers: <floor>"
)


class Refused(Exception):
    pass


def resolve(ref):
    home = os.path.expanduser("~")
    path = re.sub(r"^(?:~|\$HOME|\$\{HOME\})/", home + "/", ref)
    if (
        ".." in path.split("/")
        or os.path.islink(path)
        or not NAME.fullmatch(os.path.basename(path))
    ):
        return None
    root = next(
        (root for root in ROOTS if os.path.dirname(path) == os.path.join(home, root)),
        None,
    )
    if root is None or os.path.realpath(os.path.dirname(path)) != os.path.join(
        os.path.realpath(home), root
    ):
        return None
    return path


def listed():
    entries, section = {}, None
    for row in GRANDFATHERED.read_text().splitlines():
        if row.startswith("[") and row.endswith("]"):
            section = row[1:-1]
        elif row.strip() and section in LEGACY:
            digest, name = row.split(" ", 1)
            entries[(section, name)] = digest
    return entries


def grandfathered(path, body, action):
    home = os.path.expanduser("~")
    section, name = os.path.relpath(os.path.dirname(path), home), os.path.basename(path)
    try:
        digest = listed().get((section, name))
    except (OSError, ValueError):
        return False
    if digest != hashlib.sha256(body).hexdigest()[:32]:
        return False
    return action is None or action in LEGACY[section]


def moment(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def his_row(text):
    ids = EVIDENCE.findall(text)
    if len(ids) != 1:
        raise Refused(
            f"it needs exactly one Evidence: t3 <id of the message Alex typed>; {FORMAT}"
        )
    try:
        found = t3_yes.lookup(ids)
    except t3_yes.Unread as exc:
        raise Refused(str(exc)) from None
    if not found["messages"]:
        raise Refused(
            "its Evidence id is not a message Alex typed in T3 (web or mobile, never edited, not a scheduled task)"
        )
    message = found["messages"][0]
    age = datetime.now(timezone.utc) - moment(message["createdAt"])
    if age > LIFE or age < -timedelta(minutes=5):
        raise Refused(
            f"his message at {message['createdAt']} is not inside the last 24 h"
        )
    return message, found


def covers(text):
    words = set()
    for line in COVERS.findall(text):
        words |= {
            word.strip("`*_.,;:()").casefold() for word in re.split(r"[,\s]+", line)
        }
    return words


def covered(action, text):
    return bool(({action} | (DELETES if action in DELETES else set())) & covers(text))


def checked(path, body, text, action, command):
    if action is not None and action not in FLOORS:
        raise Refused(f"no T3 record covers the {action!r} floor; it takes an Ask line")
    if action in IRREVERSIBLE:
        raise Refused(
            f"no T3 citation lifts the {action!r} floor. " + approval_scope.HOW
        )
    if action is not None and not covered(action, text):
        raise Refused(f"its Covers line does not name {action!r}; {FORMAT}")
    message, found = his_row(text)
    if action is not None:
        approval_ask.ledger(
            found["source"],
            f"t3-{message['id']}",
            path,
            body,
            action,
            command,
            datetime.now(timezone.utc),
        )
    return {
        "basis": "t3",
        "messages": [message["id"]],
        "source": found["source"],
    }


def verdict(ref, action=None, line=None, command=None):
    path = resolve(ref)
    if path is None:
        return {
            "ok": False,
            "reason": "the record is not an owner- or alex- file in an approvals directory",
        }
    try:
        body = Path(path).read_bytes()
    except OSError:
        return {"ok": False, "reason": "the record cannot be read"}
    text = body.decode("utf-8", "replace")
    if line is not None and not any(
        row.strip().strip("`") == line for row in text.splitlines()
    ):
        return {
            "ok": False,
            "reason": "the record does not hold this exact command line on a line of its own;",
        }
    if grandfathered(path, body, action):
        return {"ok": True, "basis": "grandfathered"}
    acted = line if command is None else command
    if action is not None and approval_ask.carries(text):
        if not covered(action, text):
            return {"ok": False, "reason": f"its Covers line does not name {action!r}"}
        return approval_ask.spend(
            path, body, text, action, acted, action in IRREVERSIBLE
        )
    try:
        return {"ok": True, **checked(path, body, text, action, acted)}
    except (
        Refused,
        approval_ask.Refused,
        approval_scope.Refused,
        t3_yes.Unread,
    ) as exc:
        return {"ok": False, "reason": str(exc)}
    except (
        sqlite3.Error,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        return {"ok": False, "reason": f"the evidence could not be checked: {exc}"}


def refusal(ref, action=None, line=None, command=None):
    result = verdict(ref, action, line, command)
    return "" if result["ok"] else result["reason"]


def main(argv):
    parser = argparse.ArgumentParser(prog="approval_record")
    parser.add_argument("record")
    parser.add_argument("--action")
    parser.add_argument("--line")
    parser.add_argument("--command")
    args = parser.parse_args(argv)
    result = verdict(args.record, args.action, args.line, args.command)
    if not result["ok"]:
        result["next"] = yes_words.COVER
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
