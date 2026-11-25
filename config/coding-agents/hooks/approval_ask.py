#!/usr/bin/env python3
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ASK = re.compile(r"^[\s>*_-]*Ask\s*:(.*)$", re.M | re.I)
SHAPE = re.compile(r"\s*`?([0-9a-f]{12})\s+target\s+([a-z0-9][a-z0-9-]*):([^\s`]+)`?\s*")
PEERS = ("ax102", "ax42")
MACHINES = ("ax102", "ax42", "studio", "forge", "book", "macbook", "macbook-ts")
LIFE = timedelta(days=7)
COVERS = timedelta(hours=24)
SKEW = timedelta(minutes=5)
BUDGET = 6.0
SPENT = ".agent-rails/approvals-spent"
READ = """RAILS_HOME="$HOME/.rails" "$HOME/.rails/rails" inbox '{"ask_id":"%s"}' --json"""
SSH = ("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3")


class Refused(Exception):
    pass


def carries(text):
    return bool(ASK.search(text))


def here():
    return socket.gethostname().split(".")[0].lower()


def peers():
    return [peer for peer in os.environ.get("APPROVAL_T3_PEERS", " ".join(PEERS)).split() if peer.lower() != here()]


def named(word, text):
    return re.search(r"(?<![\w.-])" + re.escape(word) + r"(?![\w.-])", text, re.I) is not None


def parsed(text):
    lines = ASK.findall(text)
    if len(lines) != 1:
        raise Refused("it needs exactly one line Ask: <ask id> target <host>:<name>[,<name>...]")
    shape = SHAPE.fullmatch(lines[0])
    if not shape:
        raise Refused("its Ask line is not Ask: <12-hex ask id> target <host>:<name>[,<name>...]")
    names = [name for name in shape[3].split(",") if name]
    if not names:
        raise Refused("its Ask line names no target")
    return shape[1], shape[2], names


def words(command):
    try:
        return shlex.split(command, comments=True)
    except ValueError:
        return command.split()


def scoped(host, names, command):
    if host != here():
        raise Refused(f"its target is on {host}, not on {here()}")
    if command is None or not command.strip():
        raise Refused("there is no command to match against its target")
    if not any(named(name, command) for name in names):
        raise Refused(f"the command does not name any of its targets {', '.join(names)} as a word")
    if named("ssh", command) or any(named(machine, command) for machine in MACHINES if machine != here()):
        raise Refused("the command reaches another host")
    roots = [os.path.expanduser(name).rstrip("/") for name in names if "/" in name]
    if roots:
        for word in words(command):
            path = os.path.expanduser(word.split("=", 1)[-1])
            if path.startswith("/") and not any(
                path.rstrip("/") == root or path.startswith(root + "/") for root in roots
            ):
                raise Refused(f"the command names the path {word[:60]} outside its targets {', '.join(names)}")


def run(argv, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=left, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def fetched(ask_id):
    command, deadline = READ % ask_id, time.monotonic() + BUDGET
    for argv in (["sh", "-c", command], *([*SSH, peer, command] for peer in peers())):
        try:
            ask = json.loads(run(argv, deadline) or "null")["ask"]
        except (ValueError, TypeError, KeyError):
            continue
        if isinstance(ask, dict) and ask.get("id") == ask_id:
            return ask
    raise Refused(f"Rails could not be read for ask {ask_id} within {BUDGET:g} s")


def moment(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def answered(ask, host, now):
    answer = ask.get("answer")
    if ask.get("delegate") != "owner":
        raise Refused("the ask was not asked to the owner")
    if ask.get("state") != "done" or ask.get("dropped_at") is not None or not isinstance(answer, dict):
        raise Refused("the ask is not answered")
    if answer.get("answered_by") != "owner" or answer.get("source") is not None:
        raise Refused("the person did not answer the ask themselves in Inbox")
    choice, options = answer.get("choice"), ask.get("options")
    if answer.get("not_needed") is not False or not isinstance(options, list) or choice not in options:
        raise Refused("the answer is not one of the ask's options")
    if not re.match(r"yes\b", str(choice).casefold()):
        raise Refused(f"the chosen option {str(choice)[:40]!r} is not a yes")
    at = moment(answer.get("answered_at"))
    if at > now + SKEW or now - at > LIFE:
        raise Refused(f"the answer at {at.isoformat()} is more than 7 days old")
    if not named(host, str(ask.get("text") or "")):
        raise Refused(f"the question the person answered does not name {host}")


def held(entry, ask_id, target, digest, now):
    if entry.get("target") != target:
        raise Refused(f"ask {ask_id} was already spent for {entry.get('target')}")
    if entry.get("record_sha256") != digest:
        raise Refused(f"ask {ask_id} was spent by the record with sha256 {entry.get('record_sha256')}; restore it")
    ends = moment(entry.get("written") or entry.get("at")) + COVERS
    if now > ends:
        raise Refused(f"the record covered its paths for 24 h from when it was written, until {ends.isoformat()}")


def spent(ask_id, target, digest, entry, now):
    local = Path(os.path.expanduser("~")) / SPENT / f"{ask_id}.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(local, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        held(json.loads(local.read_text()), ask_id, target, digest, now)
        return
    with os.fdopen(fd, "w") as out:
        json.dump(entry, out)
        out.write("\n")


def spend(path, body, text, action, command):
    try:
        ask_id, host, names = parsed(text)
        scoped(host, names, command)
        now = datetime.now(timezone.utc)
        answered(fetched(ask_id), host, now)
        target, digest = f"{host}:{','.join(names)}", hashlib.sha256(body).hexdigest()
        written = min(now, datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc))
        entry = {"ask": ask_id, "record": path, "record_sha256": digest, "target": target, "action": action}
        spent(
            ask_id,
            target,
            digest,
            {**entry, "host": here(), "at": now.isoformat(), "written": written.isoformat()},
            now,
        )
    except Refused as exc:
        return {"ok": False, "reason": str(exc)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {"ok": False, "reason": f"the ask could not be checked: {exc.__class__.__name__}"}
    return {"ok": True, "basis": "ask", "ask": ask_id, "target": target}
