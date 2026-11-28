#!/usr/bin/env python3
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

import approval_scope  # type: ignore[missing-import]
import rails_allow  # type: ignore[missing-import]
import t3_yes  # type: ignore[missing-import]
import yes_words  # type: ignore[missing-import]

ASK = re.compile(r"^[\s>*_-]*Ask\s*:(.*)$", re.MULTILINE | re.IGNORECASE)
BOUND = re.compile(
    r"^[\s>*_-]*(?:Ask|Targets?|Covers)\s*:.*$", re.MULTILINE | re.IGNORECASE
)
SHAPE = re.compile(
    r"\s*`?([0-9a-f]{12})(?:\s+target\s+([a-z0-9][a-z0-9-]*):([^\s`]+))?`?\s*"
)
DELETES = {"delete", "data-delete"}
SAYS_DELETE = re.compile(
    r"\b(?:delete|deleting|remove|removing|erase|wipe|purge)\b", re.IGNORECASE
)
ASK_PATH = re.compile(r"(?<![\w.~-])(/[^\s'\"`,;()<>]+)")
LIFE = timedelta(days=7)
COVERS = timedelta(hours=24)
SKEW = timedelta(minutes=5)
SECONDS = 4
STATES = {"done", "waiting"}


class Refused(Exception):
    pass


def carries(text):
    return bool(ASK.search(text))


def parsed(text):
    lines = ASK.findall(text)
    if len(lines) != 1:
        raise Refused(
            "it needs exactly one line Ask: <12-hex ask id> [target <host>:<path>[,<path>...]]"
        )
    shape = SHAPE.fullmatch(lines[0])
    if not shape:
        raise Refused(
            "its Ask line is not Ask: <12-hex ask id> [target <host>:<path>[,<path>...]]"
        )
    names = [name for name in (shape[3] or "").split(",") if name]
    return shape[1], shape[2], names


def fetched(ask_id):
    try:
        ask = rails_allow.call("inbox", {"ask_id": ask_id}, SECONDS).get("ask")
    except rails_allow.Refused as exc:
        raise Refused(
            f"could not read the ask {ask_id} from one.rails ({exc})"
        ) from None
    if not isinstance(ask, dict) or ask.get("id") != ask_id:
        raise Refused(
            f"could not read the ask {ask_id} from one.rails (no such ask came back)"
        )
    return ask


def moment(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def pressed(choice, deleting):
    if deleting and choice.lstrip().lower().startswith("delete"):
        return yes_words.pressed_clear(choice)
    return yes_words.pressed_yes(choice)


def answered(ask, now, deleting, scoped):
    answer = ask.get("answer")
    if ask.get("delegate") != "owner":
        raise Refused("the ask was not asked to the owner")
    if (
        ask.get("state") not in STATES
        or ask.get("dropped_at") is not None
        or not isinstance(answer, dict)
    ):
        raise Refused("the ask is not answered")
    if answer.get("answered_by") != "owner" or answer.get("source") is not None:
        raise Refused("the person did not answer the ask themselves in Inbox")
    if answer.get("not_needed") is not False:
        raise Refused("the ask was closed as not needed")
    choice, options, typed = (
        answer.get("choice"),
        ask.get("options"),
        answer.get("text"),
    )
    if choice:
        if not isinstance(options, list) or choice not in options:
            raise Refused("the answer is not one of the ask's options")
        said, yes = str(choice), pressed(str(choice), deleting)
    elif isinstance(typed, str) and not scoped:
        said, yes = typed, not yes_words.refused(typed)
    else:
        raise Refused(
            "on this floor only a pressed option of the ask counts, and the answer is not one"
            if scoped
            else "the answer is neither one of the ask's options nor words he typed"
        )
    if not yes:
        raise Refused(
            f"the answer {said[:40]!r} is not a yes: a pressed option opens with Delete, Yes or Allow "
            "(Delete only on a delete) and holds no but, only, nothing, keep or no; typed words hold no no, "
            "not, wait, stop, later, cancel, never or question mark"
        )
    at = moment(answer.get("answered_at"))
    if at > now + SKEW or now - at > LIFE:
        raise Refused(f"the answer at {at.isoformat()} is more than 7 days old")


def bound(asked, host, names):
    if not SAYS_DELETE.search(asked):
        raise Refused("the question he answered does not name a delete")
    hosts = {
        machine
        for machine in approval_scope.MACHINES
        if re.search(rf"(?<![\w-]){machine}(?![\w-])", asked, re.IGNORECASE)
    }
    if hosts and host not in hosts:
        raise Refused(
            f"the question he answered names {', '.join(sorted(hosts))}, not {host}"
        )
    roots = [
        os.path.normpath(found.rstrip(".?!:")) for found in ASK_PATH.findall(asked)
    ]
    for name in names:
        if roots and not any(
            name == root or name.startswith(root.rstrip("/") + "/") for root in roots
        ):
            raise Refused(
                f"the record's target {name} is not inside the paths the question names"
            )


def act(action, command):
    said = " ".join((command or "").split())
    deed = "\0".join(
        (approval_scope.here(), os.getcwd(), said) if said else (action or "",)
    )
    return hashlib.sha256(deed.encode()).hexdigest()


def held(entry, key, deed, path, digest, now):
    if entry is None:
        return
    if entry.get("act") != deed:
        raise Refused(
            f"the yes {key} was already spent on another act; one yes covers one act"
        )
    if entry.get("record") != path or entry.get("record_sha256") != digest:
        raise Refused(
            f"the yes {key} was spent by the record {entry.get('record')} (sha256 {entry.get('record_sha256')}); one "
            "yes covers one record, so cite that record unchanged"
        )
    ends = moment(entry.get("at")) + COVERS
    if now > ends:
        raise Refused(
            f"the yes {key} covered its act for 24 h, until {ends.isoformat()}"
        )


def ledger(source, key, path, body, action, command, now):
    digest, deed = hashlib.sha256(body).hexdigest(), act(action, command)
    entry = {
        "key": key,
        "record": path,
        "record_sha256": digest,
        "act": deed,
        "action": action,
        "host": approval_scope.here(),
        "at": now.isoformat(),
    }
    held(t3_yes.spend(source, key, entry), key, deed, path, digest, now)


def tied(ask_id, path, text, action, now):
    binding = hashlib.sha256(
        "\n".join(line.strip() for line in BOUND.findall(text)).encode()
    ).hexdigest()
    entry = {
        "key": ask_id,
        "record": path,
        "binding": binding,
        "action": action,
        "host": approval_scope.here(),
        "at": now.isoformat(),
    }
    first = t3_yes.claim(ask_id, entry)
    if first is None:
        return
    if first.get("binding") != binding:
        raise Refused(
            f"the ask {ask_id} was first cited by the record {first.get('record')} with other Ask, Target or Covers "
            "lines; one answered ask covers one record"
        )
    if now > moment(first.get("at")) + LIFE:
        raise Refused(f"the ask {ask_id} was first used more than 7 days ago")


def spend(path, body, text, action, command, scoped):
    try:
        ask_id, host, names = parsed(text)
        now = datetime.now(timezone.utc)
        deleting = action in DELETES
        if deleting and host:
            approval_scope.listed(host, names, command)
        ask = fetched(ask_id)
        answered(ask, now, deleting, scoped)
        asked = "\n".join(str(ask.get(key) or "") for key in ("title", "text"))
        if deleting and host:
            bound(asked, host, names)
            tied(ask_id, path, text, action, now)
        else:
            if scoped:
                approval_scope.covers(asked, command)
            ledger("local", ask_id, path, body, action, command, now)
    except (Refused, approval_scope.Refused, t3_yes.Unread) as exc:
        return {"ok": False, "reason": str(exc)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {
            "ok": False,
            "reason": f"the ask could not be checked: {exc.__class__.__name__}",
        }
    return {"ok": True, "basis": "ask", "ask": ask_id}
