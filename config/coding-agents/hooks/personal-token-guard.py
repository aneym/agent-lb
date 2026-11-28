import json
import os
import re
import signal
import socket
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
MISSING = []
try:
    import approval_record  # type: ignore[missing-import]
except ImportError as error:
    approval_record = None
    MISSING.append(error.name or str(error))
try:
    import rails_allow  # type: ignore[missing-import]
except ImportError as error:
    rails_allow = None
    MISSING.append(error.name or str(error))
try:
    import t3_yes  # type: ignore[missing-import]
except ImportError as error:
    t3_yes = None
    MISSING.append(error.name or str(error))
try:
    import yes_words  # type: ignore[missing-import]
except ImportError as error:
    yes_words = None
    MISSING.append(error.name or str(error))

LOG = Path.home() / ".agent-rails/factory/personal-token-guard.jsonl"
LOG_CAP = 512 * 1024
APPROVALS = Path.home() / ".agent-rails/approvals"
APPROVAL_TTL = timedelta(hours=24)
EVIDENCE_SECONDS = 4
RECORD = "~/.agent-rails/factory/receipts/personal-vercel-tokens-2026-11-17.md"
OWNERS = {
    "gh-agent-rails-vercel-token": "Alex",
    "gh-shelf-vercel-token": "Nate",
}
NAMED = re.compile(
    r"gh[-_]agent[-_]rails[-_]vercel[-_]token|gh[-_]shelf[-_]vercel[-_]token",
    re.IGNORECASE,
)
SECRET_CALL = re.compile(r"agent-secret\b[^\n;&|]*?\b(?:get|signin)\b\s+(\S+)")
LOOSE_VERCEL = re.compile(r"vercel", re.IGNORECASE)
LOOSE_FORM = re.compile(r"[$`*?\[{]")
COVERS = re.compile(r"^[\s>*_-]*Covers\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
ALWAYS = re.compile(r"\balways\b", re.IGNORECASE)
STANDING = re.compile(
    r"^[\s>*_-]*Scope\s*:.*\b(?:standing|always)\b", re.MULTILINE | re.IGNORECASE
)
WRITE_TOOLS = {"Edit", "MultiEdit", "Write"}
READ_TOOLS = {
    "Read",
    "Grep",
    "Glob",
    "NotebookRead",
    "Edit",
    "MultiEdit",
    "Write",
    "NotebookEdit",
}
REASON = (
    "BLOCKED: {names} {verb} PERSONAL Vercel {tokens}: {owners}. "
    "No agent home or lane, Factory included, may name, read or use {them} without Alex's yes, "
    "and Nate's accounts are off limits.\n"
    "why: {why}\n"
    "next: deploy with an agent-owned token, or cite the yes Alex already gave for this one token in "
    "~/.agent-rails/approvals/alex-personal-token-<name>.md (Write tool, one token per record, Covers: <name>) with one of: "
    "Evidence: rails ask <ask id> (an owner ask naming Vercel and no other token that he answered yes; for Nate's token "
    "a pressed option naming it), Evidence: rails hold <hold id> (a hold naming the token that he allowed), or, for "
    "Alex's token only, Evidence: t3 <id of a message he typed in the last 24 h>; the guard proves he typed it and "
    "reads nothing in it, and the agent owns the call; one message covers one use, on any floor. "
    "It passes for 24 h from his answer or message, and with no expiry for Alex's token when the ask he said yes to "
    "says always or a record citing an ask or hold says Scope: standing; a record that names both tokens, an answer by an agent, "
    "or a message he did not type passes nothing. "
    "Record: " + RECORD + " {cover}"
)


def log(event, **fields):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.is_file() and LOG.stat().st_size > LOG_CAP:
            LOG.replace(LOG.with_suffix(".jsonl.1"))
        line = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            "host": socket.gethostname().split(".")[0],
            **fields,
        }
        with LOG.open("a") as handle:
            handle.write(json.dumps(line) + "\n")
    except OSError:
        pass


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def canonical(raw):
    return re.sub(r"_", "-", raw.lower())


def findings(tool, tool_input):
    names = set()
    loose = False
    for text in strings(tool_input):
        names.update(canonical(match.group(0)) for match in NAMED.finditer(text))
        if tool == "Bash":
            for match in SECRET_CALL.finditer(text):
                arg = match.group(1)
                if LOOSE_VERCEL.search(arg) and LOOSE_FORM.search(arg):
                    loose = True
    if loose and not names:
        names.update(OWNERS)
    return sorted(names)


def record_path(name):
    return APPROVALS / f"alex-personal-token-{name}.md"


def tokens(text):
    return {canonical(match.group(0)) for match in NAMED.finditer(text)}


def accepted(name, answer):
    if OWNERS[name] != "Alex":
        return answer["pressed"] and tokens(answer["said"]) == {name}
    return (
        tokens(answer["asked"]) <= {name}
        and LOOSE_VERCEL.search(answer["asked"]) is not None
    )


def evidence(text, name):
    if MISSING:
        why = (
            f"its evidence readers did not load ({', '.join(sorted(set(MISSING)))} missing from {HERE}); "
            "land claude-config main so that directory holds every hooks/ module and run scripts/link-hooks"
        )
        return None, why, ""
    via = rails_allow.cited(text)
    if via:
        answer, start, source = rails_allow.allow(text, EVIDENCE_SECONDS - 0.4)
        if not accepted(name, answer):
            if OWNERS[name] == "Alex":
                raise rails_allow.Refused(
                    f"{source} is a yes to a question that names neither Vercel nor this token, or names another token; cite the ask or hold about this one token"
                )
            raise rails_allow.Refused(
                f"{source} is not a pressed option naming this token and no other; Nate's token takes only that"
            )
        return start, source, answer["asked"]
    if OWNERS[name] != "Alex":
        raise approval_record.Refused(
            "Nate's token takes only a pressed Rails option naming it"
        )
    message, found = approval_record.his_row(text)
    key = f"t3-{message['id']}"
    entry = {
        "key": key,
        "record": str(record_path(name)),
        "act": f"token {name}",
        "action": "token",
        "host": socket.gethostname().split(".")[0].lower(),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if t3_yes.spend(found["source"], key, entry) is not None:
        raise approval_record.Refused(
            "the message of his that this record cites was already used once; a new message from him is needed"
        )
    return approval_record.moment(message["createdAt"]), "t3", ""


class Expired(BaseException):
    pass


def expired(_signum, _frame):
    raise Expired()


REFUSALS = tuple(
    module.Refused for module in (approval_record, rails_allow) if module
) + ((t3_yes.Unread,) if t3_yes else ())


def refusal(name):
    path = record_path(name)
    try:
        if path.is_symlink():
            return "its approval record is a symlink"
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "no approval record is on file"
    except OSError:
        return "its approval record cannot be read"
    covered = {
        canonical(match.group(0))
        for line in COVERS.findall(text)
        for match in NAMED.finditer(line)
    }
    named = {canonical(match.group(0)) for match in NAMED.finditer(text)}
    if covered != {name} or named != {name}:
        return (
            "its record must name this one token, on its Covers line and nowhere else"
        )
    try:
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(EVIDENCE_SECONDS)
        try:
            start, source, said = evidence(text, name)
        finally:
            signal.alarm(0)
    except Expired:
        return f"the evidence lookup passed its {EVIDENCE_SECONDS} s deadline"
    except REFUSALS as exc:
        return str(exc)
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError):
        return "its evidence could not be read"
    if start is None:
        return source
    if OWNERS[name] == "Alex" and (ALWAYS.search(said) or STANDING.search(text)):
        return None
    if datetime.now(timezone.utc) - start >= APPROVAL_TTL:
        return (
            f"Alex's yes ({source}) at {start:%Y-%m-%d %H:%M}Z is past its 24 h window"
        )
    return None


def writes_record(tool, tool_input, names):
    if tool not in WRITE_TOOLS or len(names) != 1:
        return False
    target = tool_input.get("file_path")
    if not isinstance(target, str):
        return False
    return os.path.realpath(os.path.expanduser(target)) == os.path.realpath(
        record_path(names[0])
    )


RAW = []


def main():
    RAW.append(sys.stdin.read())
    try:
        payload = json.loads(RAW[0])
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool = payload.get("tool_name", "")
    if tool != "Bash" and tool not in READ_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or {}
    found = findings(tool, tool_input)
    if writes_record(tool, tool_input, found):
        log(
            "record-write",
            tool=tool,
            names=found,
            session_id=payload.get("session_id", ""),
        )
        return 0
    reasons = {name: refusal(name) for name in found}
    names = [name for name in found if reasons[name]]
    if not names:
        if found:
            log(
                "passed",
                tool=tool,
                names=found,
                session_id=payload.get("session_id", ""),
            )
        return 0
    why = "; ".join(f"{name}: {reasons[name]}" for name in names)
    log(
        "refused",
        tool=tool,
        names=names,
        why=why,
        cwd=payload.get("cwd", ""),
        session_id=payload.get("session_id", ""),
    )
    owners = "; ".join(
        f"{name} deploys as {OWNERS[name]}'s own Vercel user" for name in names
    )
    single = len(names) == 1
    print(
        REASON.format(
            names="agent-secret " + " and ".join(names),
            verb="is a" if single else "are",
            tokens="token, not an agent token"
            if single
            else "tokens, not agent tokens",
            owners=owners,
            them="it" if single else "them",
            why=why,
            cover=yes_words.COVER if yes_words else "",
        ),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("error", error=type(exc).__name__)
        if NAMED.search("".join(RAW)) or LOOSE_VERCEL.search("".join(RAW)):
            print(
                f"BLOCKED: personal-token-guard failed ({type(exc).__name__}) on a call naming a personal Vercel "
                "token, so it cannot check Alex's yes (floor: his accounts).\n"
                "next: deploy with an agent-owned token, or fix the guard and retry.",
                file=sys.stderr,
            )
            sys.exit(2)
        sys.exit(0)
