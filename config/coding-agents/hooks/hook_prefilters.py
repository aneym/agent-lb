import os
import re


def jq_field(payload, field):
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if tool_input is None:
        return ""
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get(field)
    if value is None or value is False:
        return ""
    if not isinstance(value, str):
        return None
    return value.replace("\x00", "")


def never(_payload):
    return False


def escapes_in_input(payload):
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    if not isinstance(tool_input, dict):
        return False
    return any(isinstance(value, str) and "\\" in value for value in tool_input.values())


DANGEROUS_WORDS = ("psql", "railway", "vercel", "fly", "neonctl", "muse")
DANGEROUS_TEXT = re.compile(r"drop|truncate|launchctl", re.I)


def pf_dangerous(payload):
    command = jq_field(payload, "command")
    if command is None or not command.isascii():
        return True
    bare = re.sub(r"[\"']", "", command)
    return any(name in bare for name in DANGEROUS_WORDS) or DANGEROUS_TEXT.search(command) is not None


def pf_bootout(payload):
    command = jq_field(payload, "command")
    return command is None or "launchctl" in command


def pf_link_cli(payload):
    command = jq_field(payload, "command")
    return command is None or "link-cli" in command


def pf_railway_custody(payload):
    if not isinstance(payload, dict):
        return True
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return True
    command = tool_input.get("command", "")
    return not isinstance(command, str) or "railway" in command


def pf_no_chrome(payload):
    if not isinstance(payload, dict):
        return True
    tool_input = payload.get("tool_input")
    if tool_input is None:
        return False
    if not isinstance(tool_input, dict):
        return True
    command = tool_input.get("command")
    if command is None:
        return False
    if not isinstance(command, str) or "\x00" in command:
        return True
    try:
        from no_chrome_classify import classify  # type: ignore[missing-import]

        return classify(command) not in ("none", "pc")
    except Exception:
        return True


_WIDE_TOOL = re.compile(r"(^|[;&|(`\s])(grep\s+([^;&|]*\s)?-[a-zA-Z]*[rR]|rg\s|find\s|fd\s)", re.M)
_WIDE_PATH = re.compile(
    r"\s(/|~|~/|\$HOME/?|\"\$HOME\"/?|/(?:Users)/[^/ \t\n\r\f\v]+/?|/(?:Volumes)/[^/ \t\n\r\f\v]+/?|"
    r"/(?:Volumes)/[^/ \t\n\r\f\v]+/repos/?|"
    r"/System/?|/Library/?|/home/?|/home/[^/ \t\n\r\f\v]+/?|/home/[^/ \t\n\r\f\v]+/repos/?|/root/?)(\s|$|;|\||\))",
    re.M,
)


def pf_wide_scan(payload):
    command = jq_field(payload, "command")
    if command is None:
        return True
    if not command:
        return False
    norm = re.sub(r"[\s'\"\\]", "", command)
    places = [
        norm.lower(),
        str(payload.get("cwd") or "").lower(),
        os.getcwd().lower(),
        os.environ.get("HOME", "").lower(),
        os.path.expanduser("~").lower(),
    ]
    if "grep" in norm and any(".agent-rails" in place for place in places):
        return True
    if ".agent-rails" in command and re.search(r"(rg|grep|find|fd)\s", command):
        return True
    if _WIDE_TOOL.search(command) and _WIDE_PATH.search(command):
        return True

    return norm.count("$(") + norm.count("`") + norm.count("sh") + norm.count("env") >= 8


_SCAN_NAMES = ("rg", "find", "fd", "grep", "egrep", "fgrep", "ggrep")
_SCAN_SPLIT = re.compile(r"[\s;&|()<>`]+")


def _scan_resolve(word, cwd):
    home = os.path.expanduser("~")
    word = re.sub(r"^\$(?:HOME|\{HOME\})(?=/|$)", lambda _m: home, word)
    if word.startswith("~"):
        head, _sep, rest = word.partition("/")
        word = (home if head == "~" else "/home/" + head[1:]) + "/" + rest
    return os.path.normpath(word if word.startswith("/") else os.path.join(cwd, word))


def _scan_maybe_wide(path):
    if ".agent-rails" in path.lower():
        return True
    parts = [part for part in path.split("/") if part]
    if len(parts) <= 1:
        return True
    if parts[0] in ("Users", "home", "Volumes") and (len(parts) == 2 or (len(parts) == 3 and parts[2] == "repos")):
        return True
    if parts[:3] == ["Volumes", "StudioExt", "repos"] and len(parts) == 4 and parts[3].endswith("-wt"):
        return True
    home = os.path.expanduser("~")
    return path in (home, home + "/repos")


def pf_wide_scan_paths(payload):
    command = jq_field(payload, "command")
    if command is None:
        return True
    if not command:
        return False
    if pf_wide_scan(payload):
        return True
    cwd = payload.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        return True
    pieces, cds = {"."}, 0
    for word in _SCAN_SPLIT.split(re.sub(r"[\"'\\]", "", command)):
        if word:
            forms = {word, word.split("=", 1)[-1], word[2:] if word.startswith("-S") else word}
            pieces |= forms
            cds += bool(forms & {"cd", "pushd"})
    if not any(piece.rsplit("/", 1)[-1] in _SCAN_NAMES for piece in pieces):
        return False
    if ".." in command and re.search(r"[\"']", command):
        return True
    candidates = {cwd or os.getcwd(), os.getcwd()}
    if cds:
        candidates.add(os.path.expanduser("~"))
    if cds > 4:
        return True
    for _round in range(cds):
        if len(candidates) * len(pieces) > 4096:
            return True
        candidates |= {_scan_resolve(piece, base) for base in candidates for piece in pieces}
    return any(_scan_maybe_wide(_scan_resolve(piece, base)) for base in candidates for piece in pieces)


def _guard_shell_tokens(text):
    import shlex

    cleaned, quote, escaped, boundary, comment = [], None, False, True, False
    for char in text:
        if comment:
            if char != "\n":
                continue
            comment = False
        if escaped:
            escaped = False
            boundary = False
        elif char == "\\" and quote != "'":
            escaped = True
            boundary = False
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            boundary = False
        elif char == "#" and boundary:
            comment = True
            continue
        else:
            boundary = char in " \t\r\n;&|()"
        cleaned.append(char)
    lexer = shlex.shlex("".join(cleaned), posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def pf_wide_scan_parse(payload):
    command = jq_field(payload, "command")
    if command is None:
        return True
    if not command:
        return False
    if pf_wide_scan_paths(payload):
        return True
    if "<<" in command or "$(" in command or "`" in command:
        return True
    flat = re.sub(r"[\s\"'\\]", "", command)
    if sum(flat.count(word) for word in ("sh", "env", "watch")) >= 8:
        return True
    if "'" in command or '"' in command:
        try:
            tokens = _guard_shell_tokens(command)
        except ValueError:
            return True
        if any("'" in token or '"' in token for token in tokens):
            return True
    return False


def pf_display_wake(payload):
    if not isinstance(payload, dict):
        return True
    name = str(payload.get("tool_name") or payload.get("tool") or "")
    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
    if "DISPLAY_WAKE_SKIP" in command:
        return False
    return re.search(r"computer[_-]?use|cua-driver|screencapture", name + "\n" + command, re.I) is not None


def pf_jev_alert(_payload):
    return True


def _prettier_config_near(start):
    seen = set()
    for top in (start, os.path.realpath(start)):
        path = top
        while True:
            if path not in seen:
                seen.add(path)
                try:
                    names = os.listdir(path)
                except OSError:
                    return True
                for name in names:
                    if name.startswith(".prettierrc") or name.startswith("prettier.config"):
                        return True
                for name in ("package.json", "package.yaml"):
                    if name in names:
                        try:
                            with open(os.path.join(path, name), "rb") as handle:
                                if b"prettier" in handle.read():
                                    return True
                        except OSError:
                            return True
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
    return False


def pf_prettier(payload):
    target = jq_field(payload, "file_path")
    if target is None:
        return True
    if not target or not re.search(r"\.(ts|tsx|js|jsx|json|css|md)$", target):
        return False
    directory = os.path.dirname(target) or "."
    if not os.path.isabs(directory) and os.environ.get("CDPATH"):
        return True
    directory = os.path.abspath(directory)
    if not os.path.isdir(directory):
        return False
    return _prettier_config_near(directory)


SCRIPT_PREFILTERS = {
    "agent-lb-bootout-guard.sh": ("8f4a0eea5f7639e7d0f6e8d02a048ffd00db3c37daa9b6f4acb86d612a36d304", pf_bootout),
    "display-wake.sh": ("05fa9781d2a413543b2432e583035168b968f1bc0353d03276d97c3105e1121e", pf_display_wake),
    "wide-scan-guard.sh": ((), pf_wide_scan_parse),
    "link-cli-guard.sh": (
        (
            "95b3c4c5e241ef572b859b21a14fac3f1407f1d4787fc6c18e97b6c1182534fd",
            "0e98a75ac8a82786d97350a0c5b6469021cbc6eb3eeb4c56a1c85b2443e7edb6",
        ),
        pf_link_cli,
    ),
    "dangerous-command-guard.sh": ((), pf_dangerous),
    "no-chrome-guard.sh": ((), pf_no_chrome),
    "railway-vars-guard.sh": ("9fa0e824554830e501bda0d9cb8a21213e6011935101b26f25e03a99796818dd", pf_railway_custody),
    "route.sh": ("36bbdd44a69543ff7fe2865985443a2a09ab2f540d6bf09df25ec6c978e1d629", pf_jev_alert),
}

SCRIPT_NEEDS = {
    "wide-scan-guard.sh": (("cat",), ("jq",), ("python3",)),
    "agent-lb-bootout-guard.sh": (("/opt/homebrew/bin/jq", "jq"), ("grep",)),
    "railway-vars-guard.sh": (("cat",), ("python3",)),
    "link-cli-guard.sh": (("jq",), ("grep",)),
    "dangerous-command-guard.sh": (("jq",), ("grep",), ("python3",)),
    "no-chrome-guard.sh": (("/opt/homebrew/bin/jq", "jq"), ("/usr/bin/python3",)),
}

COMMAND_PREFILTERS = {
    "55c67a61ea18f3d6d58b572fe09f1699073e09ddbbc05b2d15ccf853937501d7": pf_prettier,
}
