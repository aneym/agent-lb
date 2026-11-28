import json
import os
import shlex
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PEERS = ("ax102", "ax42")
SENDERS = {"web", "mobile"}
STORE = ".t3/userdata/statev2.sqlite"
SPENT = ".agent-rails/approvals-spent"
TYPED = "SELECT payload_json FROM orchestration_v2_projection_messages WHERE role='user' AND message_id IN ({marks})"
BUDGET = 3.0


class Unread(Exception):
    pass


def typed(message):
    return (
        message.get("createdBy") == "user"
        and message.get("creationSource") in SENDERS
        and not message.get("scheduledTaskId")
        and message.get("updatedAt") == message.get("createdAt")
    )


def read(ids):
    me = socket.gethostname().split(".")[0].lower()
    store = Path(os.path.expanduser("~")) / STORE
    if not store.is_file():
        raise Unread(f"the T3 store on {me} is missing")
    try:
        with sqlite3.connect(store.as_uri() + "?mode=ro", uri=True, timeout=3) as conn:
            rows = conn.execute(TYPED.format(marks=",".join("?" * len(ids))), list(ids))
            return [m for m in (json.loads(p) for (p,) in rows) if typed(m)]
    except (sqlite3.Error, ValueError) as exc:
        raise Unread(
            f"the T3 store on {me} could not be read ({exc.__class__.__name__})"
        ) from None


def claim(key, entry):
    if not key.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"the ledger key {key[:40]!r} is not a plain id")
    ledger = Path(os.path.expanduser("~")) / SPENT / f"{key}.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return json.loads(ledger.read_text())
    with os.fdopen(fd, "w") as out:
        json.dump(entry, out)
        out.write("\n")
    return None


def peers():
    me = socket.gethostname().split(".")[0].lower()
    names = os.environ.get("APPROVAL_T3_PEERS", " ".join(PEERS)).split()
    return [peer for peer in names if peer.lower() != me]


def remote(peer, spec, deadline):
    left = deadline - time.monotonic()
    if left <= 0.2:
        raise Unread(f"T3 on {peer} could not be read within {BUDGET:g} s")
    try:
        done = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=2",
                peer,
                "python3 - " + shlex.quote(json.dumps(spec)),
            ],
            input=Path(__file__).read_text(),
            capture_output=True,
            text=True,
            timeout=left,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise Unread(f"T3 on {peer} could not be read within {BUDGET:g} s") from None
    except OSError as exc:
        raise Unread(
            f"T3 on {peer} could not be read ({exc.__class__.__name__})"
        ) from None
    try:
        answer = json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise Unread(f"T3 on {peer} could not be read (ssh exit {done.returncode})")
    return answer


def lookup(ids):
    ids, unread = sorted(set(ids)), []
    try:
        found = read(ids)
    except Unread as exc:
        found, unread = [], [str(exc)]
    if found:
        return {"messages": found, "source": "local"}
    deadline = time.monotonic() + BUDGET
    for peer in peers():
        try:
            more = remote(peer, {"ids": ids}, deadline)
        except Unread as exc:
            unread.append(str(exc))
            continue
        if more.get("messages"):
            return {"messages": more["messages"], "source": peer}
    if unread:
        raise Unread(
            "; ".join(unread) + ", so whether he typed it is unknown, not disproved"
        )
    return {"messages": [], "source": "local"}


def spend(source, key, entry):
    if source == "local":
        return claim(key, entry)
    answer = remote(source, {"claim": key, "entry": entry}, time.monotonic() + BUDGET)
    if "held" not in answer:
        raise Unread(f"the spent ledger on {source} could not be written")
    return answer["held"]


if __name__ == "__main__":
    spec = json.loads(sys.argv[1])
    if "claim" in spec:
        print(json.dumps({"held": claim(spec["claim"], spec["entry"])}))
    else:
        print(json.dumps({"messages": read(spec.get("ids") or [])}))
