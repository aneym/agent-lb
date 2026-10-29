import os
import re
import shlex
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from no_chrome_argv import parse  # type: ignore
from no_chrome_doubt import kind  # type: ignore[missing-import]
from no_chrome_python import isolated_launches  # type: ignore[missing-import]
from no_chrome_shell import (  # type: ignore
    INTERPRETERS,
    KEYWORDS,
    SCP_VALUE,
    SHELLS,
    SSH_VALUE,
    Doubt,
    find_execs,
    heredoc_split,
    host_of,
    normalize,
    operands,
    owner_path,
    pin_home,
    profile_tainted,
    reads_stdin,
    record_writes,
    segment_values,
    shell_string,
    simple_commands,
    substitutions,
)

PC_HOSTS = {"pc", "pc-wsl"}

MAC_BIN = re.compile(
    r"(?i)(Google Chrome|Chromium|Aside|Safari|Firefox|Brave Browser|Microsoft Edge|Vivaldi|(?<![\w.-])(Arc|Opera|Orion))[^/]*\.app/Contents/MacOS"
)
BROWSER_BIN = re.compile(
    r"(?i)(google-chrome|chromium|chrome|firefox|brave|microsoft-edge|msedge|vivaldi|opera|safari|epiphany)(-browser)?(-(stable|beta|dev|unstable|canary|nightly|esr|bin))?(\.exe)?"
)
MAC_APP = re.compile(
    r"(?i)(^|/)(google chrome|chromium|chrome|aside|safari|firefox|brave browser|microsoft edge|arc|opera|vivaldi|orion)(\.app)?/?$"
)
APPLE_CODE = re.compile(
    r"(?i)\bapp(lication)?\s*(\(\s*|\s+(id\s+)?)[\"']?(google chrome|chrome|chromium|safari|aside|com\.google\.chrome|com\.apple\.safari|org\.chromium|firefox|brave browser|microsoft edge|vivaldi|arc|opera|orion|org\.mozilla\.firefox|com\.brave\.browser|com\.microsoft\.edgemac)"
    r"|\bopen\s+location\b|openLocation|\bopen\s+(-[a-z]+\s+)*-[a-z]*[ab][a-z]*\s+[\"']?([^\s\"']*/)?(google chrome|chrome|chromium|safari|aside|com\.)"
)
ARGV_OPEN = re.compile(
    r"(?i)[\"']([^\"'\s]*/)?((xdg-|gnome-|kde-)?open5?|gio|wslview)[\"']\s*,[^\])]*?(google chrome|chrome|chromium|safari|aside|firefox|brave|microsoft edge|vivaldi"
    r"|[\"'](https?|file|ftp|about|data|javascript|view-source):|\.(x?html?|shtml|webarchive|webloc|url)[\"'])"
    r"|[\"']([^\"'\s]*/)?(google-chrome(-stable)?|chromium(-browser)?|chrome|firefox|brave(-browser)?|microsoft-edge(-stable|-beta|-dev)?|vivaldi(-stable)?|sensible-browser|x-www-browser|www-browser)[\"']\s*[,\]]"
)
OPEN_CODE = re.compile(
    r"(?i)(?<![\w.-])((xdg-|gnome-|kde-)?open5?|gio\s+open|wslview)\s+(-\S+\s+)*[\"']?((https?|file|ftp|about|data|javascript|view-source):"
    r"|[^\s\"']*\.(x?html?|shtml|webarchive|webloc|url)\b)"
)
URLISH = re.compile(
    r"(?i)^(https?|file|ftp|about|data|javascript|view-source|chrome|blob):|\.(x?html?|shtml|mht|mhtml|webarchive|webloc|inetloc|url)/?$|[$`]"
)
OPENERS = {
    "open",
    "xdg-open",
    "gio",
    "wslview",
    "gnome-open",
    "kde-open",
    "kde-open5",
    "sensible-browser",
    "x-www-browser",
    "www-browser",
}
BROWSERS = {"sensible-browser", "x-www-browser", "www-browser"}
CODE_READERS = {"awk", "gawk", "mawk", "nawk", "sed", "gsed"}
RUNS = {
    "sed": re.compile(r"(?<![A-Za-z_])e|[^\w\s][gpiImM0-9]*[gpiImM0-9]e"),
    "awk": re.compile(r"\bsystem\b|\|"),
}
BROWSER_WORD = re.compile(
    r"(?i)safari|chrom|aside|firefox|brave|microsoft.?edge|edgemac|vivaldi|\barc\b|\bopera\b|\borion\b"
)
BUNDLE = re.compile(
    r"(?i)com\.google\.chrome|com\.apple\.safari|org\.chromium|aside|org\.mozilla\.firefox|com\.brave\.browser|com\.microsoft\.edgemac|com\.vivaldi\.vivaldi|company\.thebrowser\.browser|com\.operasoftware\.opera|com\.kagi\.kagimacOS"
)
AGENT_BROWSER = "agent-browser"
AGENT_BROWSER_REACH = (
    "--auto-connect",
    "--cdp",
    "--profile",
    "--state",
    "--restore",
    "--session-name",
    "--headed",
    "--extension",
    "--executable-path",
    "--args",
    "--config",
    "--provider",
    "--engine",
)
AGENT_BROWSER_SUBS = {"connect", "auth", "profiles", "state", "-p"}
LAUNCHER = "agent-cdp-launch"
SPACE = "mac-space"
LANE_TOOL = "lane-browser"
LANE_SUBS = {
    "shot",
    "outdir",
    "stop",
    "open",
    "goto",
    "tab-new",
    "click",
    "dblclick",
    "fill",
    "select",
    "type",
    "press",
    "hover",
    "check",
    "uncheck",
    "eval",
    "snapshot",
    "console",
    "tab-close",
    "tab-select",
    "resize",
    "set-color-scheme",
    "screenshot",
    "requests",
    "tab-list",
    "list",
    "go-back",
    "go-forward",
    "reload",
    "close",
}
SESSION = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
PLAIN = re.compile(r"[A-Za-z0-9_./:=%+,@ -]+")
LAUNCH_CODE = re.compile(
    r"\bwebbrowser\b|puppeteer\.launch|chromium\.launch|launchPersistentContext|launch_persistent_context|storage_state\s*=|--storage-state|--user-data-dir|\bconnectOverCDP\s*\(|\bconnect_over_cdp\s*\(|--cdp\b|--remote-debugging-port\b|wss?://[^\s\"']+:\d+/devtools"
)
DATA_READERS = {
    "echo",
    "printf",
    "git",
    "grep",
    "egrep",
    "rg",
    "cat",
    "sed",
    "awk",
    "jq",
    "gh",
    "tee",
    "head",
    "tail",
    "less",
    "wc",
    "sort",
    "diff",
    "ls",
    "stat",
    "file",
    "which",
    "type",
    "man",
    "du",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "test",
    "[",
    "review-ledger",
    "factory",
    "unblock",
    "codex",
    "claude",
    "codex-seat",
    "true",
    ":",
}


def agent_profile(value):
    home = os.path.expanduser("~")
    value = value.replace("${TMPDIR}", os.environ.get("TMPDIR", "")).replace("$TMPDIR", os.environ.get("TMPDIR", ""))
    value = value.replace("${HOME}", home).replace("$HOME", home)
    if value.startswith("~/"):
        value = home + value[1:]
    if not os.path.isabs(value) or any(c in value for c in "$`\\"):
        return False
    path = os.path.realpath(value)
    owner_library = os.path.realpath(home + "/Library")
    if path == owner_library or path.startswith(owner_library + "/"):
        return False
    roots = [
        "/tmp",
        "/private/tmp",
        os.environ.get("TMPDIR", ""),
        home + "/.agent-rails",
        home + "/.open-factory",
    ]
    return any(root and path.startswith(os.path.realpath(root).rstrip("/") + "/") for root in roots)


def agent_headless(argv):
    args = argv[1 : argv.index("--")] if "--" in argv else argv[1:]
    profiles = [a.partition("=")[2] for a in args if a.startswith("--user-data-dir=")]
    profiles += [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--user-data-dir"]
    if not any(a in ("--headless", "--headless=new", "--headless=old", "--headless=true") for a in args):
        return False
    if any(
        a.startswith(
            (
                "--remote-debugging",
                "--storage-state",
                "--load-extension",
                "--profile-directory",
            )
        )
        for a in args
    ):
        return False
    return len(profiles) == 1 and agent_profile(profiles[0])


def mac_launch(argv, writes=()):
    name = os.path.basename(argv[0])
    if name in ("ssh", "scp"):
        ops = operands(argv[1:], SSH_VALUE if name == "ssh" else SCP_VALUE)
        targets = ops[:1] if name == "ssh" else [arg for arg in ops if ":" in arg or arg.startswith("scp://")]
        if not any(host_of(target) in PC_HOSTS for target in targets):
            return False
    if argv[1:] == ["--help"]:
        return False
    if name == AGENT_BROWSER:
        words = [w for a in argv[1:] for w in a.split()]
        if words[-1:] == ["batch"] or "--stdin" in words:
            return True
        return any(w in AGENT_BROWSER_SUBS or w.startswith(AGENT_BROWSER_REACH) for w in words)
    if name == "playwright-cli":
        args = argv[1:]
        if any(a in ("--extension", "connect") or a.startswith("--extension=") for a in args):
            return True
        if any(a.startswith(("--storage-state", "--cdp", "--remote-debugging")) for a in args):
            return True
        browsers = [a.partition("=")[2] for a in args if a.startswith("--browser=")]
        browsers += [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--browser"]
        if browsers or any(a.startswith("--user-data-dir") for a in args):
            return not agent_headless(argv)
        return "--headless" not in args
    paths = [os.path.normpath(a) if "/" in a and not a.startswith("-") else a for a in argv]
    if BROWSER_BIN.fullmatch(os.path.basename(paths[0])) or MAC_BIN.search(paths[0]):
        return not agent_headless(argv)
    if MAC_BIN.search(" ".join(paths[:2])):
        return True
    if name in BROWSERS:
        return True
    if name in OPENERS - {"open"}:
        return any(URLISH.search(a) for a in argv[1:] if not a.startswith("-"))
    if name == "open":
        i = 1
        while i < len(argv) and argv[i] != "--args":
            arg, path = argv[i], paths[i]
            if URLISH.search(arg) or not arg.startswith("-") and MAC_APP.search(path):
                return True
            if arg.startswith("-") and not arg.startswith("--") and ("a" in arg[1:] or "b" in arg[1:]):
                if i + 1 < len(argv) and (
                    MAC_APP.search(paths[i + 1]) or "b" in arg[1:] and BUNDLE.search(argv[i + 1])
                ):
                    return True
                i += 1
            i += 1
    runs = RUNS["sed" if "sed" in name else "awk"] if name in CODE_READERS else None
    codes = parse(name, argv[1:])[0] if name in INTERPRETERS or runs else []
    if (name in INTERPRETERS or runs and any(runs.search(a) for a in codes)) and any(
        launch_code(a, name, writes) for a in codes
    ):
        return True
    return name not in DATA_READERS and any(
        a.startswith("--remote-debugging-port")
        or a == "--headless"
        or a.startswith(("--headless=", "--user-data-dir"))
        or (name != "page-shot" and a.startswith("--storage-state"))
        for a in argv[1:]
    )


def dedot(body):
    body = re.sub(r"/(\.?/)+", "/", body)
    while True:
        out = re.sub(r"/(?!\.\.?/)[^/\"'\s]+/\.\./", "/", body)
        if out == body:
            return re.sub(r"/\.(?=[\"'\s]|$)", "", out)
        body = out


def launch_code(body, name=None, writes=()):
    if name in ("python", "python3"):
        body = isolated_launches(
            body,
            lambda path: agent_profile(path) and not profile_tainted(["chromium", "--user-data-dir=" + path], writes),
        )
    body = re.sub(r"\\+(?=[\"'])", "", dedot(body))
    return bool(
        LAUNCH_CODE.search(body)
        or APPLE_CODE.search(body)
        or MAC_BIN.search(body)
        or ARGV_OPEN.search(body)
        or OPEN_CODE.search(body)
        or any(
            owner_path(value, {"HOME": os.path.expanduser("~")}) for value in re.findall(r"[\"']([^\"']+)[\"']", body)
        )
        or name == "osascript"
        and BROWSER_WORD.search(body)
    )


def launchers(tool=LAUNCHER):
    home = os.path.expanduser("~")
    return {
        tool,
        home + "/.local/bin/" + tool,
        home + "/.agent-rails/factory-runtime/bin/" + tool,
    }


def guest_space(argv, plain):
    trusted = argv[0] in launchers(SPACE)
    if argv[1:2] in (["driver"], ["stop"]) and trusted:
        return True
    return None


def lane_tool(argv, first):
    home = os.path.expanduser("~")
    link, source = (
        home + "/.local/bin/" + LANE_TOOL,
        home + "/.agent-rails/overlay/engine/bin/" + LANE_TOOL,
    )
    if not first or argv[0] not in (LANE_TOOL, link):
        return False
    if argv[0] == LANE_TOOL and os.path.normpath(shutil.which(LANE_TOOL) or "") != link:
        return False
    if not (os.path.islink(link) and os.path.isfile(source) and os.path.realpath(link) == os.path.realpath(source)):
        return False
    rest = argv[3:] if argv[2:3] == ["--"] else argv[2:]
    if len(argv) < 3 or not SESSION.fullmatch(argv[1]) or not rest or rest[0] not in LANE_SUBS:
        return False
    flags = [a for a in rest[1:] if a.startswith("-")]
    return not flags or rest[0] == "shot" and all(a in ("--width", "--dark") for a in flags)


def classify(text, depth=0):
    if depth > 4:
        raise Doubt
    if depth == 0:
        text = pin_home(text)
    original = text
    text, live_bodies, code = heredoc_split(text)
    found = {"none"}
    bodies, text = substitutions(text)
    live, _ = substitutions(live_bodies)
    for body in bodies + live:
        found.add(classify(body, depth + 1))
    redirects = []
    array_records = []
    commands = list(simple_commands(text, redirects, array_records))
    array_records = {id(raw): values for raw, values in array_records}
    arrays = {}
    variables = {
        "HOME": os.path.expanduser("~"),
        "TMPDIR": os.environ.get("TMPDIR", ""),
    }
    writes = []
    if any(owner_path(value, variables) for value in redirects):
        found.add("mac")
    plain = depth == 0 and not (bodies or live or code) and len(commands) == 1 and PLAIN.fullmatch(original)
    sole = depth == 0 and not (bodies or live or code)
    for index, (raw, here, piped) in enumerate(commands):
        if id(raw) in array_records:
            key = raw[0].removesuffix("=")
            arrays.setdefault(key, []).append(segment_values(array_records[id(raw)], variables))
            continue
        argv = raw
        while argv and argv[0] in KEYWORDS:
            argv = argv[1:]
        if any(a.startswith("AGENT_BROWSER_") and "=" in a for a in argv):
            found.add("mac")
        segment_values(argv, variables)
        argv = normalize(argv)
        if not argv:
            continue
        argv = segment_values(argv, variables)
        name = os.path.basename(argv[0])
        paths = argv
        if name in {"codex", "claude", "codex-seat", "echo", "printf"}:
            paths = []
        elif name == "git" and "commit" in argv[1:]:
            paths = []
            message = False
            for arg in argv:
                if message:
                    message = False
                elif arg in ("-m", "--message"):
                    message = True
                elif not arg.startswith(("--message=", "-m")):
                    paths.append(arg)
        if any(
            owner_path(value.partition("=")[2] if value.startswith("--") and "=" in value else value, variables)
            for value in paths
        ):
            found.add("mac")
        if name in {"cp", "ln", "rsync", "ditto", "tar", "mv"} and any(
            owner_path(value, variables, True) for value in argv[1:]
        ):
            found.add("mac")
        if profile_tainted(argv, writes):
            found.add("mac")
        record_writes(argv, writes)
        if lane_tool(argv, sole and index == 0 and argv == raw and not here):
            continue
        if name == LANE_TOOL or name == "lb" and argv[2:3] and argv[2] in LANE_SUBS:
            found.add("mac")
            continue
        if LAUNCHER in (name, os.path.basename(raw[0])):
            if not (plain and argv == raw and argv[0] in launchers()):
                found.add("mac")
            continue
        if name == SPACE:
            allowed = guest_space(argv, plain and argv == raw)
            if allowed is not None:
                if not allowed:
                    found.add("mac")
                continue
        if name == "seat-run" and "--" in argv[1:]:
            nested = argv[argv.index("--") + 1 :]
            if nested:
                optional = re.fullmatch(r"\$\{([A-Za-z_]\w*)\[@\]\+\$\{\1\[@\]\}\}", nested[0])
                if optional:
                    choices = arrays.get(optional[1])
                    wrapper = [os.path.expanduser("~/.local/bin/verify-slot"), "codex-verifier", "--"]
                    if not choices or any(value not in ([], wrapper) for value in choices):
                        raise Doubt
                    nested = nested[1:]
            if not nested:
                raise Doubt
            found.add(classify(shlex.join(nested), depth + 1))
        if mac_launch(argv, writes):
            found.add("mac")
        elif name in ("playwright-cli", AGENT_BROWSER):
            continue
        elif name == "find":
            for segment in find_execs(argv[1:]):
                found.add(classify(shlex.join(segment), depth + 1))
        elif name not in DATA_READERS | INTERPRETERS | {"ssh", "scp", "seat-run"}:
            for i in range(1, len(argv)):
                if argv[i - 1] in ("--brief", "--prompt", "--message", "-m"):
                    continue
                child = os.path.basename(argv[i])
                if child in INTERPRETERS | SHELLS | OPENERS | {
                    "eval",
                    "codex",
                    "claude",
                    "codex-seat",
                } or MAC_BIN.search(dedot(argv[i])):
                    found.add(classify(shlex.join(argv[i:]), depth + 1))
                    break
        for body in here if reads_stdin(name, argv[1:], False) else []:
            if name in SHELLS:
                found.add(classify(body, depth + 1))
            elif launch_code(body, name, writes):
                found.add("mac")
        if piped and reads_stdin(name, argv[1:]):
            raise Doubt
        if name == "pc-chrome-ext":
            found.add("browser")
        elif name in SHELLS:
            script = shell_string(argv[1:])
            if script is not None:
                found.add(classify(script, depth + 1))
        elif name == "eval":
            found.add(classify(" ".join(argv[1:]), depth + 1))
        elif name in ("ssh", "scp"):
            ops = operands(argv[1:], SSH_VALUE if name == "ssh" else SCP_VALUE)
            targets = ops[:1] if name == "ssh" else [o for o in ops if ":" in o or o.startswith("scp://")]
            if any(host_of(t) in PC_HOSTS for t in targets):
                found.add("browser" if any("chrome" in a.lower() for a in argv[1:]) else "pc")
    for consumer, body in code:
        if consumer not in ("python", "python3") and kind(body) == "browser":
            found.add("mac")
        if consumer in SHELLS:
            found.add(classify(body, depth + 1))
        elif launch_code(body, consumer, writes):
            found.add("mac")
    if "mac" in found and "pc" in found and "browser" not in found:
        return "macpc"
    for result in ("browser", "mac", "pc"):
        if result in found:
            return result
    return "none"


if __name__ == "__main__":
    try:
        print(classify(sys.argv[1]))
    except (Doubt, ValueError, OSError):
        print(kind(sys.argv[1]))
