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

HERE = Path(__file__).resolve().parent
GRANDFATHERED = HERE / "approval_grandfathered.txt"
ROOTS = (".agent-rails/approvals", ".open-factory/approvals", ".agent-rails/lanes/orchestrator-refs")
LEGACY = {"destructive": ".agent-rails/lanes/orchestrator-refs"}
NAME = re.compile(r"(?:owner|alex)-[A-Za-z0-9._-]+\.md")
UUID = re.compile(r"(?<![0-9a-f-])(?:mcp:)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f-])")
QUOTE = re.compile(r"""^[\s>*_-]*Quote[s]?\s*:\s*[*_]*\s*["“](.+)["”]""", re.M | re.I)
COVERS = re.compile(r"^[\s>*_-]*Covers\s*:\s*(.+)$", re.M | re.I)
EXPIRES = re.compile(r"^[\s>*_-]*Expires\s*:\s*`?([0-9][0-9T:.+-]*Z?)`?\s*$", re.M | re.I)
SENDERS = {"web", "mobile"}
SHORTEST = 12
LIFE = timedelta(hours=24)
LONGEST = timedelta(days=7)
FORMAT = (
    'a record needs Evidence: t3 <thread id> <message id>, Quote: "<Alex\'s exact words>", '
    "Covers: <action> and optionally Expires: <ISO time, at most 7 days after his message>"
)


def resolve(ref):
    home = os.path.expanduser("~")
    path = re.sub(r"^(?:~|\$HOME|\$\{HOME\})/", home + "/", ref)
    if ".." in path.split("/") or os.path.islink(path) or not NAME.fullmatch(os.path.basename(path)):
        return None
    if os.path.dirname(path) not in {os.path.join(home, root) for root in ROOTS}:
        return None
    return path


def plain(text):
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.casefold().split())


def grandfathered(path, body, action):
    root = os.path.join(os.path.expanduser("~"), LEGACY.get(action, ".agent-rails/approvals"))
    if action is not None and os.path.dirname(path) != root:
        return False
    try:
        listed = set(GRANDFATHERED.read_text().split())
    except OSError:
        return False
    return hashlib.sha256(body).hexdigest()[:32] in listed


def messages(ids):
    store = Path(os.path.expanduser("~")) / ".t3/userdata/statev2.sqlite"
    if not store.is_file():
        raise LookupError("this host has no T3 store to check the evidence against")
    keys = sorted({key for value in ids for key in (value, "mcp:" + value)})
    marks = ",".join("?" * len(keys))
    with sqlite3.connect(store.as_uri() + "?mode=ro", uri=True, timeout=3) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM orchestration_v2_projection_messages WHERE role='user' "
            f"AND (thread_id IN ({marks}) OR message_id IN ({marks}))",
            keys + keys,
        ).fetchall()
    found = []
    for (payload,) in rows:
        message = json.loads(payload)
        if message.get("createdBy") == "user" and message.get("creationSource") in SENDERS:
            found.append(message)
    return found


def said(message):
    return datetime.fromisoformat(message["createdAt"].replace("Z", "+00:00"))


def evidence(text):
    quotes = [plain(match) for match in QUOTE.findall(text)]
    if not quotes:
        return None, "it quotes no words of Alex's"
    if any(len(quote.replace(".", "").strip()) < SHORTEST for quote in quotes):
        return None, f"each quote must hold at least {SHORTEST} characters of Alex's words"
    ids = set(UUID.findall(text))
    if not ids:
        return None, "it names no T3 thread or message"
    found = messages(ids)
    matched = []
    for quote in quotes:
        parts = [part.strip(" .") for part in re.split(r"\.\.\.|…", quote) if part.strip(" .")]
        hits = [m for m in found if all(part in plain(m.get("text", "")) for part in parts)]
        if not hits:
            return None, f"no message Alex typed in the named T3 threads holds the quote {quote[:60]!r}"
        matched.append(max(hits, key=said))
    return matched, ""


def expiry(text, matched):
    start = min(said(message) for message in matched)
    stated = EXPIRES.search(text)
    if not stated:
        return start + LIFE
    moment = datetime.fromisoformat(stated[1].replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return min(moment, start + LONGEST)


def covers(text):
    words = set()
    for line in COVERS.findall(text):
        words |= {word.strip("`*_.,;:()").casefold() for word in re.split(r"[,\s]+", line)}
    return words


def verdict(ref, action=None, line=None):
    path = resolve(ref)
    if path is None:
        return {"ok": False, "reason": "the record is not an owner- or alex- file in an approvals directory"}
    try:
        body = Path(path).read_bytes()
    except OSError:
        return {"ok": False, "reason": "the record cannot be read"}
    text = body.decode("utf-8", "replace")
    if line is not None and not any(row.strip().strip("`") == line for row in text.splitlines()):
        return {"ok": False, "reason": "the record does not hold this exact command line"}
    if grandfathered(path, body, action):
        return {"ok": True, "basis": "grandfathered"}
    try:
        matched, reason = evidence(text)
    except (LookupError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "reason": f"the evidence could not be checked: {exc}"}
    if matched is None:
        return {"ok": False, "reason": f"{reason}; {FORMAT}"}
    try:
        until = expiry(text, matched)
    except ValueError:
        return {"ok": False, "reason": "its Expires line is not an ISO time"}
    result = {"basis": "t3", "messages": [m["id"] for m in matched], "expires": until.isoformat()}
    if until <= datetime.now(timezone.utc):
        return {**result, "ok": False, "reason": f"it expired at {until.isoformat()}"}
    if action is not None and line is None and action not in covers(text):
        return {**result, "ok": False, "reason": f"its Covers line does not name {action!r}; {FORMAT}"}
    return {**result, "ok": True}


def refusal(ref, action=None, line=None):
    result = verdict(ref, action, line)
    return "" if result["ok"] else result["reason"]


def main(argv):
    parser = argparse.ArgumentParser(prog="approval_record")
    parser.add_argument("record")
    parser.add_argument("--action")
    parser.add_argument("--line")
    args = parser.parse_args(argv)
    result = verdict(args.record, args.action, args.line)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
