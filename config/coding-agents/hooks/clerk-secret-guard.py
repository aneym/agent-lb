import json
import os
import pwd
import re
import socket
import sys
import time
from pathlib import Path

from hook_reply import warn
from shell_commands import invoked

LOG = Path.home() / ".agent-rails/factory/clerk-secret-guard.jsonl"
LOG_CAP = 512 * 1024
ALLOWED_HOMES = {"factory"}
HOME_ROOTS = (".agent-rails/agents", ".t3-rails-demo/agents")
PRODUCT_REPO = re.compile(r"^(?:agent-apps-[a-z0-9-]+|(?!factory-)[a-z0-9-]+-plugin(?:-wt)?|shelf(?:-wt)?)$")
NAMED = re.compile(
    r"clerk-rails-prod-secret|custody|test/token\.sh|(?:^|[\s/;&|(\"'])token\.sh\b|clerk_session|accounts_clerk|rails_costs_clerk|rails-costs-read",
    re.I,
)
SECRET_CALL = re.compile(r"agent-secret\b[^\n;&|]*?\b(?:get|signin)\b\s+(\S+)")
LOOSE_NAME = re.compile(r"[$`*?\[{]|clerk", re.I)
FED = re.compile(r"xargs\b[^\n;&|]*agent-secret")
CUSTODY = re.compile(r"(?:^|[\s/\"'=])\.agent-rails/custody\b|(?:^|[\s\"'=])custody/")
READER = r"(?:(?:bash|sh|zsh|python3?|node|tsx|bun|npx\s+tsx|uv\s+run)\s+)?\S*(?:test/token\.sh|token\.sh|clerk_session\.py|accounts_clerk\.ts|rails-costs-read)\b"
READ_TOOLS = {"Read", "Grep", "Glob", "NotebookRead", "Edit", "MultiEdit", "Write", "NotebookEdit"}
OPEN_TOOLS = {"Read", "Grep", "Glob", "NotebookRead"}
REASON = (
    "BLOCKED (secret floor): the prod Clerk secret (agent-secret clerk-rails-prod-secret) and the paths that read it "
    "(agent-secret's custody store, rails-one test/token.sh, test/clerk_session.py, test/accounts_clerk.ts, "
    "Factory's rails-costs reader) are closed to product agent homes; this session runs from {home}.\n"
    "next: test sign-ins run from a rails-one build lane (a rails-one checkout or worktree) or the Factory home; "
    "ask Factory for one. Record: ~/.agent-rails/factory/receipts/clerk-secret-scope-2026-11-17.md"
)


def log(event, **fields):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.is_file() and LOG.stat().st_size > LOG_CAP:
            LOG.replace(LOG.with_suffix(".jsonl.1"))
        line = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event,
                "host": socket.gethostname().split(".")[0], **fields}
        with LOG.open("a") as handle:
            handle.write(json.dumps(line) + "\n")
    except OSError:
        pass


def account_home():
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


def roots(home):
    found = [home / root for root in HOME_ROOTS]
    extra = os.environ.get("T3CODE_AGENT_HOMES_DIR")
    if extra:
        found.append(Path(extra))
    return [Path(os.path.realpath(root)) for root in found]


def product_place(raw, home):
    if not raw:
        return None
    path = Path(os.path.realpath(os.path.expanduser(raw)))
    for root in roots(home):
        try:
            rest = path.relative_to(root).parts
        except ValueError:
            continue
        if rest and rest[0] not in ALLOWED_HOMES:
            return f"agent home {rest[0]}"
        return None
    for part in path.parts:
        if PRODUCT_REPO.match(part):
            return f"product repo {part}"
    return None


def product_session(payload):
    home = account_home()
    env_home = os.environ.get("HOME")
    if env_home and os.path.realpath(env_home) != os.path.realpath(home):
        return f"agent HOME {Path(env_home).name}"
    for raw in (os.environ.get("CLAUDE_PROJECT_DIR"), payload.get("cwd")):
        place = product_place(raw, home)
        if place:
            return place
    return None


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def findings(tool, tool_input):
    texts = list(strings(tool_input))
    found = []
    for text in texts:
        if NAMED.search(text):
            found.append("names-secret-or-reader")
        if tool == "Bash":
            for match in SECRET_CALL.finditer(text):
                if LOOSE_NAME.search(match.group(1)):
                    found.append("agent-secret-unnamed-or-clerk")
            if FED.search(text):
                found.append("agent-secret-fed")
    return sorted(set(found))


def reads_secret(tool, tool_input, found):
    if tool == "Bash":
        command = tool_input.get("command") or ""
        if {"agent-secret-unnamed-or-clerk", "agent-secret-fed"} & set(found):
            return True
        return invoked(command, READER) or bool(CUSTODY.search(command))
    if tool in OPEN_TOOLS:
        place = " ".join(str(tool_input.get(key) or "") for key in ("file_path", "notebook_path", "path", "pattern"))
        return bool(CUSTODY.search(" " + place))
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool = payload.get("tool_name", "")
    if tool != "Bash" and tool not in READ_TOOLS:
        return 0
    found = findings(tool, payload.get("tool_input") or {})
    if not found:
        return 0
    place = product_session(payload)
    if not place:
        return 0
    if not reads_secret(tool, payload.get("tool_input") or {}, found):
        log("warned", place=place, tool=tool, found=found, session_id=payload.get("session_id", ""))
        return warn("clerk-secret-guard (warn, the call runs): it names the prod Clerk secret or a reader of it, "
                    "but reads no secret. Running a reader or reading the secret from %s is refused." % place)
    log("refused", place=place, tool=tool, found=found, session_id=payload.get("session_id", ""))
    print(REASON.format(home=place), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("error", error=type(exc).__name__)
        sys.exit(0)
