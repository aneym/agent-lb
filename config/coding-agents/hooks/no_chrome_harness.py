import os
import re
import shutil

from no_chrome_shell import DURATION, HARNESS_TOOL  # type: ignore

HARNESS_SUBS = {"shot", "signin", "outdir", "stop"}
HARNESS_LEAD_ASSIGNMENTS = {"LANE_DIR", "AGENT_RAILS_LANE", "TZ", "LANG", "LC_ALL"}
HARNESS = {"browser-harness", "browser-harness-mcp"}
HARNESS_FORBIDDEN_WORDS = (
    "BU_",
    "BH_",
    "BROWSER_USE",
    "putenv",
    "unsetenv",
    "daemon",
    "subprocess",
    "os.system",
    "popen",
    "spawn",
    "execv",
    "__import__",
    "importlib",
    "_ipc",
    "admin",
    "remote_debugging",
    "chrome://",
)
HARNESS_FORBIDDEN = re.compile(
    "|".join(map(re.escape, HARNESS_FORBIDDEN_WORDS)) + r"|environb?(?![a-z])", re.IGNORECASE
)
HARNESS_SAFE = (
    ["--version"],
    ["--help"],
    ["-h"],
    ["skill"],
    ["telemetry", "status"],
    ["telemetry", "disable"],
    ["recordings"],
    ["recordings", "disable"],
)
SESSION = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def harness_lead(words):
    i = 0
    while i < len(words):
        name, equals, value = words[i].partition("=")
        if equals and name in HARNESS_LEAD_ASSIGNMENTS and not HARNESS_FORBIDDEN.search(value):
            i += 1
        elif words[i] == "timeout" and i + 1 < len(words) and DURATION.fullmatch(words[i + 1]):
            i += 2
        else:
            return False
    return True


def harness_tool(argv):
    home = os.path.expanduser("~")
    link = home + "/.local/bin/" + HARNESS_TOOL
    source = home + "/.agent-rails/rails-host-runtime/current/packages/agent-runtime/bin/" + HARNESS_TOOL
    if argv[0] not in (HARNESS_TOOL, link):
        return False
    if argv[0] == HARNESS_TOOL:
        resolved = shutil.which(HARNESS_TOOL)
        if resolved is None:
            if os.environ.get("PATH") != "/usr/bin:/bin:/usr/sbin:/sbin":
                return False
        elif os.path.normpath(resolved) != link:
            return False
    if not (os.path.islink(link) and os.path.isfile(source) and os.path.realpath(link) == os.path.realpath(source)):
        return False
    if argv[1:] == ["--list"] or argv[1:] in (["--help"], ["-h"]):
        return True
    return len(argv) >= 2 and SESSION.fullmatch(argv[1]) is not None and (len(argv) == 2 or argv[2] in HARNESS_SUBS)


def harness_refused(argv, raw, here):
    literal = [os.path.expanduser(word) if word.startswith("~/") else word for word in raw]
    lead = literal[: len(literal) - len(argv)]
    return (
        literal[len(lead) :] != argv
        or not harness_lead(lead)
        or not harness_tool(argv)
        or any(HARNESS_FORBIDDEN.search(body) for body in here)
    )
