#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRANDFATHERED = HERE / "approval_grandfathered.txt"
ROOTS = (".agent-rails/approvals", ".open-factory/approvals", ".agent-rails/lanes/orchestrator-refs")
LEGACY = {
    ".agent-rails/approvals": {"delete", "stash", "browser", "provider", "data-delete"},
    ".agent-rails/lanes/orchestrator-refs": {"destructive"},
    ".open-factory/approvals": set(),
}
SEALED = {"destructive", "provider", "data-delete"}
ORIGIN = "https://rails.so"
TOKEN = "approval-records-token"
NAME = re.compile(r"(?:owner|alex)-[A-Za-z0-9._-]+\.md")
UUID = re.compile(r"(?<![0-9a-f-])(?:mcp:)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f-])")
APR = re.compile(r"(?<![A-Za-z0-9_])apr_[0-9a-f]{8,64}(?![A-Za-z0-9_])")
QUOTE = re.compile(r"""^[\s>*_-]*Quote[s]?\s*:\s*[*_]*\s*["“](.+)["”]""", re.M | re.I)
COVERS = re.compile(r"^[\s>*_-]*Covers\s*:\s*(.+)$", re.M | re.I)
EXPIRES = re.compile(r"^[\s>*_-]*Expires\s*:(.*)$", re.M | re.I)
MOMENT = re.compile(r"\s*`?(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?)`?\s*")
SENDERS = {"web", "mobile"}
FRAGMENT = 12
TOTAL = 30
LIFE = timedelta(hours=24)
LONGEST = timedelta(days=7)
FORMAT = (
    'a record needs Evidence: t3 <thread id> <message id>, Quote: "<Alex\'s exact words>", '
    "Covers: <action> and optionally Expires: <ISO time, at most 7 days after his message>"
)


class Refused(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve(ref):
    home = os.path.expanduser("~")
    path = re.sub(r"^(?:~|\$HOME|\$\{HOME\})/", home + "/", ref)
    if ".." in path.split("/") or os.path.islink(path) or not NAME.fullmatch(os.path.basename(path)):
        return None
    root = next((root for root in ROOTS if os.path.dirname(path) == os.path.join(home, root)), None)
    if root is None or os.path.realpath(os.path.dirname(path)) != os.path.join(os.path.realpath(home), root):
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


def token():
    home = os.path.expanduser("~")
    search = os.pathsep.join([home + "/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])
    secret = shutil.which("agent-secret", path=search)
    if secret is None:
        raise Refused("agent-secret is not installed, so the sealed record cannot be read")
    done = subprocess.run(
        [secret, "get", TOKEN],
        capture_output=True,
        text=True,
        timeout=8,
        env={"HOME": home, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    if done.returncode or not done.stdout.strip():
        raise Refused(f"this host holds no {TOKEN}, so the sealed record cannot be read")
    return done.stdout.strip()


def status(record_id):
    paths = ssl.get_default_verify_paths()
    cafile = paths.openssl_cafile if os.path.isfile(paths.openssl_cafile or "") else None
    capath = paths.openssl_capath if os.path.isdir(paths.openssl_capath or "") else None
    if cafile is None and capath is None:
        raise Refused("this host has no system CA bundle to check rails.so against")
    context = ssl.create_default_context(cafile=cafile, capath=capath)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect()
    )
    request = urllib.request.Request(
        f"{ORIGIN}/api/mobile/v1/approval-records/{record_id}", headers={"authorization": "Bearer " + token()}
    )
    try:
        with opener.open(request, timeout=6) as answer:
            return json.loads(answer.read(1 << 16))
    except urllib.error.HTTPError as exc:
        raise Refused(f"Rails answered {exc.code} for {record_id}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Refused(f"Rails could not be reached for {record_id}: {exc}") from None


def moment(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def sealed(text, action):
    ids = sorted(set(APR.findall(text)))
    if not ids:
        raise Refused(f"the {action} floor takes only a Rails sealed record: Evidence: rails apr_<id>")
    reasons = []
    for record_id in ids:
        row = status(record_id)
        want = None if action is None else "floor." + action
        if row.get("record_id") != record_id:
            reasons.append(f"{record_id} came back as another record")
        elif row.get("status") != "active" or row.get("spent_at") is not None:
            reasons.append(f"{record_id} is {row.get('status')}{' and spent' if row.get('spent_at') else ''}")
        elif row.get("source_kind") != "ask_card":
            reasons.append(f"{record_id} was not minted from Alex's card decision")
        elif not row.get("expires_at") or moment(row["expires_at"]) <= datetime.now(timezone.utc):
            reasons.append(f"{record_id} has expired")
        elif want is not None and row.get("action") != want:
            reasons.append(f"{record_id} is for {row.get('action')!r}, not {want!r}")
        else:
            return {"basis": "rails", "records": [record_id], "expires": row["expires_at"]}
    raise Refused("; ".join(reasons))


def messages(ids):
    store = Path(os.path.expanduser("~")) / ".t3/userdata/statev2.sqlite"
    if not store.is_file():
        raise Refused("this host has no T3 store to check the evidence against")
    keys = sorted(ids)
    marks = ",".join("?" * len(keys))
    with sqlite3.connect(store.as_uri() + "?mode=ro", uri=True, timeout=3) as conn:
        rows = conn.execute(
            f"SELECT payload_json FROM orchestration_v2_projection_messages WHERE role='user' AND message_id IN ({marks})",
            keys,
        ).fetchall()
    found = []
    for (payload,) in rows:
        message = json.loads(payload)
        if message.get("createdBy") == "user" and message.get("creationSource") in SENDERS:
            found.append(message)
    return found


def spaced(text):
    return " ".join(text.split())


def fragments(quote):
    parts = [spaced(part) for part in re.split(r"\.\.\.|…", quote)]
    parts = [part for part in parts if part]
    weight = [len(part.replace(" ", "")) for part in parts]
    if not parts or min(weight) < FRAGMENT or sum(weight) < TOTAL:
        raise Refused(
            f"each quoted fragment needs {FRAGMENT} or more letters of Alex's words and the quote {TOTAL} or more"
        )
    return parts


def ordered(parts, text):
    at = 0
    for part in parts:
        at = text.find(part, at)
        if at < 0:
            return False
        at += len(part)
    return True


def evidence(text):
    quotes = QUOTE.findall(text)
    if not quotes:
        raise Refused("it quotes no words of Alex's")
    pieces = [fragments(quote) for quote in quotes]
    ids = set(UUID.findall(text))
    found = messages(ids) if ids else []
    if not found:
        raise Refused("it names no T3 message id of a message Alex typed")
    matched = []
    for quote, parts in zip(quotes, pieces):
        hits = [m for m in found if ordered(parts, spaced(m.get("text", "")))]
        if not hits:
            raise Refused(f"no named message Alex typed holds the quote {quote[:60]!r} in order")
        matched.append(max(hits, key=lambda m: moment(m["createdAt"])))
    return matched


def expiry(text, matched):
    start = min(moment(message["createdAt"]) for message in matched)
    stated = EXPIRES.findall(text)
    if not stated:
        return start + LIFE
    ends = []
    for value in stated:
        found = MOMENT.fullmatch(value)
        if not found:
            raise Refused(f"its Expires line {value.strip()[:40]!r} is not a bare ISO time")
        try:
            ends.append(moment(found[1]))
        except ValueError:
            raise Refused(f"its Expires line {value.strip()[:40]!r} is not a bare ISO time") from None
    return min(min(ends), start + LONGEST)


def covers(text):
    words = set()
    for line in COVERS.findall(text):
        words |= {word.strip("`*_.,;:()").casefold() for word in re.split(r"[,\s]+", line)}
    return words


def quoted(text, action, line):
    matched = evidence(text)
    until = expiry(text, matched)
    result = {"basis": "t3", "messages": [m["id"] for m in matched], "expires": until.isoformat()}
    if until <= datetime.now(timezone.utc):
        raise Refused(f"it expired at {until.isoformat()}")
    if action is not None and line is None and action not in covers(text):
        raise Refused(f"its Covers line does not name {action!r}; {FORMAT}")
    return result


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
        if action in SEALED or (action is None and APR.search(text)):
            return {"ok": True, **sealed(text, action)}
        return {"ok": True, **quoted(text, action, line)}
    except Refused as exc:
        return {"ok": False, "reason": str(exc)}
    except (sqlite3.Error, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {"ok": False, "reason": f"the evidence could not be checked: {exc}"}


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
