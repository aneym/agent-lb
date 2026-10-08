#!/usr/bin/env python3
"""One process per Claude Code hook entry: run every guard of one (event, matcher) entry, give one answer.

Why (studio-load-fix, 2026-10-07): each Bash call ran 14 PreToolUse hooks and 1 PostToolUse hook, each through
/bin/sh into bash, python or jq, across ~56 sessions; about 300 new processes a second at peak. install-policy.py
(`--hook-dispatcher on`) folds the per-hook config of PreToolUse, PostToolUse, UserPromptSubmit and Stop into
dispatch/registry.json beside this file and leaves one settings entry per matcher:
    python3 "$HOME/.claude/hooks/hook-dispatch.py" <Event> '<matcher>'
Claude Code still does the matching; this process runs the hooks of that one matcher, in config order.

How each hook runs (doubt keeps the old path: its own process through /bin/sh -c, with its own timeout):
- inproc: a Python guard on the registry's reviewed list (`inproc`) whose source has no hazard (fork, exec, threads,
  exit handlers, signals, a bare except) runs inside this process with its own argv, stdin, stdout, stderr, fds 0-2,
  __main__, sys.path[0] and timer; the shell wrappers around it (`2>/dev/null`, `|| true`,
  `|| { printf %s '<json>'; }`) are applied to its result.
- filtered: a shell guard on the pinned list (exact script or command bytes, sha256) is skipped when its prefilter
  proves it cannot act on this input (the prefilter is a superset of every input the guard blocks or acts on);
  otherwise the real script runs. A changed script loses its pin and always runs.
- rewriter: `rtk hook claude` (side-effect free, never blocks). It is skipped when a guard already denied or a later
  guard rewrote the input (that rewrite wins either way); when it is the only process still needed and every other
  guard answered nothing, this process execs into it, so the usual Bash call costs one process per event. The exec'd
  rewriter carries its own timeout as a timer armed before the exec (build lead decision (a), 2026-10-08): one that
  hangs or answers late is killed by SIGALRM at its own timeout, so a late rewrite never lands; Claude Code shows
  that as a failed-hook notice, where per-hook it cancelled the rewriter with nothing shown. The payload file is
  unlinked before the exec (fd 0 keeps it readable), so nothing is left behind.
- external: everything else, exactly as configured.

Answer: one guard with output passes through byte for byte, except an exit 2 without a JSON block reason, which
is answered as exit 2 plus the JSON block Claude Code would have built (`[<guard command>]: <stderr>`), so the
message the model sees keeps the guard's own command. Several: any block wins and the block messages are joined in
config order; JSON answers merge (deny > ask > allow, contexts joined, the last updatedInput in config order wins);
plain stdout and errors follow Claude Code's per-event rules. Claude Code (2.1.293, probed 2026-10-07) reads a hook's
stdout as JSON whatever its exit code and then ignores its stderr, so one process cannot answer with JSON and also
raise a non-blocking error notice. A guard that failed without a JSON answer (exit 1, a crash) while another answered
with JSON therefore has its notice, as Claude Code words it (`Failed with non-blocking status code: <stderr>`, or
`No stderr output`), carried in the merged answer's systemMessage, in config order, so the user still sees it;
without any JSON answer the dispatcher exits with the failed guard's code and the notices on stderr, one line each.

Failing closed (2026-10-07 post-merge review of 4e1cea13; p13D: any dispatcher error denies, never skips): on
PreToolUse, where the guards can deny, a broken registry or entry list, a registry of another fold, a temp file that
cannot be written, guard answers that cannot be merged and any uncaught error refuse the call with exit 2 and a
reason, never a silent exit 0 or 1. A floor guard (FLOOR_SCRIPTS, FLOOR_COMMANDS) that times out, crashes, is missing
or prints something that is not a hook answer refuses the call too, even where its per-hook wrapper failed open;
its exit 2 under a `|| true` or `|| { printf ...; }` wrapper still blocks. Every other guard fails open as before,
and each failure is logged to dispatch/failures.jsonl. The other events' guards are notices and side effects (every
one is `|| true` today), so there the dispatcher's own faults give a non-blocking notice (exit 1), as a failed guard
did per-hook; a refused UserPromptSubmit would erase every prompt of the session.

Registry revisions: install-policy names each fold `registry.<rev>.json` and puts the rev in every settings entry
(`... <Event> '<matcher>' <rev>`), so settings and the guards they run switch in one atomic settings write and a
session still on older settings keeps the guards it started with. A registry whose entries no longer hash to its
rev is damaged and never answers. Each candidate registry of the entry's own fold is
tried in turn and the first whose entry for this matcher is a valid hook list wins, so a damaged copy falls back to a
good copy of the same fold: the rev's own file, then registry.json and its backup when they carry that rev; for an
entry without a rev, registry.json and its backup when they have none, then registry.legacy.json (the fold entries
without a rev were written with, kept by install-policy when it first writes revs). A registry of another fold never
answers, since it may lack a guard the entry's fold had; no valid entry refuses (PreToolUse). Rev files are kept
30 days.

Rollback: `python3 ~/.agents/policy/coding-agents/install-policy.py --hook-dispatcher off` restores the per-hook
config verbatim from the registry. Must stay Python 3.9 compatible: `python3` may resolve to /usr/bin/python3.
"""
import builtins
import hashlib
import io
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import types

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
DEFAULT_TIMEOUT = 600.0  # seconds; Claude Code's default for a command hook
SHELL = "/bin/sh"  # Claude Code spawns command hooks with shell: true (observed: /bin/sh -c '<command>')
HERE = os.path.dirname(os.path.abspath(__file__))
REWRITERS = {"rtk hook claude"}  # side-effect free, never blocks; may be skipped or exec'd into
REV = re.compile(r"[0-9a-f]{12}")
# Floor guards (p13D and the simplify lead, 2026-10-07): PreToolUse guards that hold a kept floor (destructive
# commands, secrets, merges, relays, seats), and wide-scan-guard.sh, which the U1 spec names beside kill-guard and the
# seat guard as a deny case that must hold (2026-10-08 review M1: it failed open). On PreToolUse one that times out,
# crashes, is missing or answers with something that is not a hook answer refuses the call, whatever the per-hook
# config made of that failure (a `|| true` or `|| { printf ...; }` wrapper included); any other guard fails open as
# before, and the failure is logged to dispatch/failures.jsonl. Matched by script name anywhere in the command, or by
# the exact inline command.
FLOOR_SCRIPTS = ("workflow-seat-guard.py", "workflow-relay-guard.py", "seat-guard.py", "rm-dynamic-deny",
                 "stash-guard", "railway-vars-guard.sh", "link-cli-guard.sh", "plutil-guard.sh", "kill-guard",
                 "wide-scan-guard.sh")
FLOOR_WORD = re.compile(r"(?:^|[/\s\"'])(%s)(?=$|[\s\"';|&)])" % "|".join(re.escape(name) for name in FLOOR_SCRIPTS))
FLOOR_COMMANDS = {  # sha256 of the exact inline command -> its name
    "0fbf6582d067534e0cf7179a1801b12c675c75397170a48d36aec72171483e7c": "dangerous-command",
}
FLOOR_TEXT = "BLOCKED: Dangerous command"  # the inline leaf's own refusal, in any later version of its text
NOTICE_PREFIX = "Failed with non-blocking status code: "  # what Claude Code puts before a failed hook's stderr
# Environment that changes what a pinned shell guard does before or while it runs (a startup file, shell options,
# exported functions that replace jq, grep or echo, injected libraries). With any of it set the prefilter's proof
# does not hold, so the guard runs.
ENV_SENSITIVE = ("BASH_ENV", "ENV", "BASHOPTS", "SHELLOPTS", "POSIXLY_CORRECT", "GREP_OPTIONS", "GLOBIGNORE",
                 "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH")

# Source text that makes in-process execution unsafe or different: a forked or exec'd copy of this process, threads
# that outlive the guard, exit handlers, signal handling, or a handler that could swallow the timer's exception.
HAZARD = re.compile(
    r"os\._exit|os\.fork|os\.exec|os\.setsid|os\.dup2|os\.closerange|os\.kill\(\s*os\.getpid|threading|_thread|"
    r"multiprocessing|concurrent\.futures|asyncio|atexit|\bsignal\.|faulthandler|ctypes|sys\.settrace|sys\.setprofile|"
    r"except\s*:|except\s+BaseException|except\s*\(\s*BaseException|sys\.modules\[\s*['\"]__main__|importlib\.reload|"
    r"sys\.__std(?:in|out|err)__|os\.close\(\s*[012]\s*\)")
# dup2 onto fd 0, 1 or 2 is undone after the guard (fds 0-2 are saved and restored); any other target is a hazard.
DUP2_STD = re.compile(r"os\.dup2\((?:[^()]|\([^()]*(?:\([^()]*\))?[^()]*\))*,\s*[012]\s*\)")
IMPORT_NAMES = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))", re.M)

_PATH_WORD = (r'"\$HOME/[^"$`\\]*"|\$HOME/[^\s"\'$`\\;&|<>(){}*?\[\]]*|~/[^\s"\'$`\\;&|<>(){}*?\[\]]*|'
              r'"/[^"$`\\]*"|/[^\s"\'$`\\;&|<>(){}*?\[\]]+')
_ARG_WORD = r"[A-Za-z0-9_./=:+-]+"
SHAPE = re.compile(
    r"^\s*(?:(?P<interp>/usr/bin/python3|python3)\s+)?(?P<script>%s)(?P<args>(?:\s+%s)*)"
    r"(?P<devnull>\s+2>\s*/dev/null)?"
    r"(?:\s+\|\|\s+(?:(?P<true>true)|\{\s*printf\s+%%s\s+'(?P<fb>[^']*)'\s*;\s*\}))?\s*$" % (_PATH_WORD, _ARG_WORD))
# The same wrappers at the end of any command (2026-10-08 review M2): a floor guard whose command SHAPE does not read
# (`python3 -u "<guard>" 2>/dev/null || true`) still runs bare, so its own failure is seen before the wrapper hides it.
WRAPPER_TAIL = re.compile(
    r"(?P<devnull>\s+2>\s*/dev/null)?"
    r"(?:\s+\|\|\s+(?:(?P<true>true)|\{\s*printf\s+%s\s+'(?P<fb>[^']*)'\s*;\s*\}))?\s*$")


class Result(object):
    __slots__ = ("code", "out", "err", "timed_out", "mode", "spawned", "floor_failure")

    def __init__(self, code=0, out=b"", err=b"", timed_out=False, mode="", spawned=0):
        self.code, self.out, self.err, self.timed_out = code, out, err, timed_out
        self.mode, self.spawned = mode, spawned
        self.floor_failure = None  # why a floor guard failed (timeout, crash, missing, malformed), or None

    def empty(self):
        """Nothing Claude Code acts on: exit 0 and no stdout (stderr of an exit-0 hook is ignored)."""
        return not self.timed_out and self.code == 0 and not self.out


class _GuardTimeout(BaseException):
    pass


# ---------------------------------------------------------------------------------------------------- environment


def registry_path():
    return os.environ.get("HOOK_DISPATCH_REGISTRY") or os.path.join(HERE, "dispatch", "registry.json")


def env_sensitive():
    """The first variable in the environment that a prefilter's proof does not cover, or None."""
    for key in ENV_SENSITIVE:
        if key in os.environ:
            return key
    for key in os.environ:
        if key.startswith("BASH_FUNC_"):
            return key
    return None


def which(name):
    return shutil.which(name, path=os.environ.get("PATH", os.defpath))


def utf8_locale():
    for key in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(key)
        if value:
            return bool(re.search(r"utf-?8", value, re.I))
    return False


def expand_word(word):
    if word.startswith('"') and word.endswith('"'):
        word = word[1:-1]
    home = os.environ.get("HOME", "")
    if word.startswith("$HOME/"):
        return home + word[len("$HOME"):]
    if word.startswith("~/"):
        return home + word[1:]
    return word


def interpreter_of(script):
    """The interpreter a direct exec of script runs (its #! line), or None."""
    try:
        with open(script, "rb") as handle:
            first = handle.readline(256)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].decode("utf-8", "replace").split()
    if not parts:
        return None
    if os.path.basename(parts[0]) == "env" and len(parts) > 1:
        return parts[1]
    return parts[0]


def same_interpreter(interp):
    if interp is None:
        return False
    path = interp if os.path.isabs(interp) else which(interp)
    if not path:
        return False
    try:
        return os.path.realpath(path) == os.path.realpath(sys.executable)
    except OSError:
        return False


def python_like(interp):
    return interp is not None and re.fullmatch(r"python3(\.\d+)?", os.path.basename(interp)) is not None


def sha256_file(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------------- prefilters
# Each returns False only when the guard provably does nothing for this input (exit 0, no output, no side effect).
# Pinned to the exact bytes reviewed; any other version of the guard always runs.


def _jq_field(payload, field):
    """What `jq -r '.tool_input.<field> // empty'` hands the shell guard: '' for nothing, None for 'do not reason'."""
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
    return value.replace("\x00", "")  # bash $(...) drops NUL bytes


def escapes_in_input(payload):
    """A backslash anywhere in the tool input. An `echo "$CMD"` under a shell or option with xpg_echo (macOS /bin/sh,
    a bash built or started with it) turns escapes into other text (`l\\x69nk-cli` into `link-cli`), which no
    prefilter reads; such an input always runs the pinned guard."""
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    if not isinstance(tool_input, dict):
        return False
    return any(isinstance(value, str) and "\\" in value for value in tool_input.values())


def pf_dangerous(payload):
    command = _jq_field(payload, "command")
    if command is None:
        return True
    # grep -iE "rm\s+-rf\s+/|DROP\s+(DATABASE|TABLE)"; [\ss] also covers a grep that reads \s as a literal s.
    return re.search(r"rm[\ss]+-rf[\ss]+/|drop[\ss]+(database|table)", command, re.I) is not None


def pf_bootout(payload):
    command = _jq_field(payload, "command")
    return command is None or "launchctl" in command


def pf_link_cli(payload):
    command = _jq_field(payload, "command")
    return command is None or "link-cli" in command


def pf_railway(payload):
    command = _jq_field(payload, "command")
    return command is None or "railway" in command


def pf_no_chrome(payload):
    command = _jq_field(payload, "command")
    if command is None:
        return True
    # Its block list: Chrome.app/Chromium.app binaries, `open -a` Chrome/Chromium, --remote-debugging-port,
    # --headless, puppeteer.launch, chromium.launch, launchPersistentContext; since factory f76e54a5a (2026-10-07)
    # also ssh/scp to Alex's PC with `chrome` (any case) in the command. Every Chrome or Chromium name contains
    # "chrom" in some case, so this superset holds for both reviewed versions. Since factory 03b60384a the guard
    # unquotes words (chr'ome', a backslash-newline inside the word), so the test reads the command with quotes,
    # backslashes and newlines removed.
    literals = ("--remote-debugging-port", "--headless", "puppeteer.launch", "launchPersistentContext")
    return any(text in command for text in literals) or "chrom" in re.sub(r"[\"'\\\n]", "", command).lower()


_WIDE_TOOL = re.compile(r"(^|[;&|(`\s])(grep\s+([^;&|]*\s)?-[a-zA-Z]*[rR]|rg\s|find\s|fd\s)", re.M)
_WIDE_PATH = re.compile(
    # A superset of the guard's own list: any home under /(Users)/ and any repos dir on any volume.
    r"\s(/|~|~/|\$HOME/?|\"\$HOME\"/?|/(?:Users)/[^/ \t\n\r\f\v]+/?|/(?:Volumes)/[^/ \t\n\r\f\v]+/?|"
    r"/(?:Volumes)/[^/ \t\n\r\f\v]+/repos/?|"
    r"/System/?|/Library/?|/home/?|/home/[^/ \t\n\r\f\v]+/?|/home/[^/ \t\n\r\f\v]+/repos/?|/root/?)(\s|$|;|\||\))",
    re.M)


def pf_wide_scan(payload):
    command = _jq_field(payload, "command")
    if command is None:
        return True
    if not command:
        return False
    norm = re.sub(r"[\s'\"\\]", "", command)
    places = [norm.lower(), str(payload.get("cwd") or "").lower(), os.getcwd().lower(),
              os.environ.get("HOME", "").lower(), os.path.expanduser("~").lower()]
    if "grep" in norm and any(".agent-rails" in place for place in places):
        return True  # the FIFO rule: a grep-family command whose path resolves under .agent-rails
    if ".agent-rails" in command and re.search(r"(rg|grep|find|fd)\s", command):
        return True  # bulletin lookups over ~/.agent-rails
    if _WIDE_TOOL.search(command) and _WIDE_PATH.search(command):
        return True  # recursive search over /, home, a volume root or all repos
    # Its scanner refuses at nesting depth 9; every level needs one of these constructs.
    return norm.count("$(") + norm.count("`") + norm.count("sh") + norm.count("env") >= 8


_SCAN_NAMES = ("rg", "find", "fd", "grep", "egrep", "fgrep", "ggrep")
_SCAN_SPLIT = re.compile(r"[\s;&|()<>`]+")


def _scan_resolve(word, cwd):
    """wide-scan-guard.sh resolve_path: $HOME and ~ expanded, relative words joined to cwd, normalised."""
    home = os.path.expanduser("~")
    word = re.sub(r"^\$(?:HOME|\{HOME\})(?=/|$)", lambda _m: home, word)
    if word.startswith("~"):
        head, _sep, rest = word.partition("/")
        word = (home if head == "~" else "/home/" + head[1:]) + "/" + rest
    return os.path.normpath(word if word.startswith("/") else os.path.join(cwd, word))


def _scan_maybe_wide(path):
    """A superset of the guard's wide_root plus its FIFO rule's `.agent-rails` component test."""
    if ".agent-rails" in path.lower():
        return True
    parts = [part for part in path.split("/") if part]
    if len(parts) <= 1:  # /, /System, /Library, /home, /root and every other top directory
        return True
    if parts[0] in ("Users", "home", "Volumes") and (len(parts) == 2 or (len(parts) == 3 and parts[2] == "repos")):
        return True
    home = os.path.expanduser("~")
    return path in (home, home + "/repos")


def pf_wide_scan_paths(payload):
    """wide-scan-guard.sh since agent-lb d402b454 (2026-10-08): it acts only when a search tool (rg, find, fd or a
    grep) is the command of some segment and one of its path words resolves, from the payload cwd or a `cd` earlier
    in the command, to a wide root or into .agent-rails; or past nesting depth 8. This reads every word of the
    command, quotes removed, as both a possible tool and a possible path, and every word as a possible `cd` target
    (one round per `cd`), so it is a superset; the older versions' rules (pf_wide_scan) are kept beside it."""
    command = _jq_field(payload, "command")
    if command is None:
        return True
    if not command:
        return False
    if pf_wide_scan(payload):
        return True
    cwd = payload.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        return True
    pieces, cds = {"."}, 0  # "." too: a search with no path searches its cwd (bb83dc35)
    for word in _SCAN_SPLIT.split(re.sub(r"[\"'\\]", "", command)):
        if word:
            # env -S'<command>' and --split-string=<command> carry a command inside one word.
            forms = {word, word.split("=", 1)[-1], word[2:] if word.startswith("-S") else word}
            pieces |= forms
            cds += bool(forms & {"cd", "pushd"})
    if not any(piece.rsplit("/", 1)[-1] in _SCAN_NAMES for piece in pieces):
        return False
    if ".." in command and re.search(r"[\"']", command):
        return True  # a quoted word with a space split above, whose `..` the guard resolves whole
    candidates = {cwd or os.getcwd(), os.getcwd()}
    if cds:
        candidates.add(os.path.expanduser("~"))  # a bare `cd` goes home
    if cds > 4:
        return True
    for _round in range(cds):
        if len(candidates) * len(pieces) > 4096:
            return True
        candidates |= {_scan_resolve(piece, base) for base in candidates for piece in pieces}
    return any(_scan_maybe_wide(_scan_resolve(piece, base)) for base in candidates for piece in pieces)


def _guard_shell_tokens(text):
    """wide-scan-guard.sh shell_tokens, verbatim: comments dropped, then shlex with shell punctuation."""
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
    """wide-scan-guard.sh since agent-lb bb83dc35 (2026-10-08) also refuses any input it cannot parse (an unfinished
    heredoc, substitution or quote, at any nesting level), nests through `watch`, follows `pushd` and a bare `cd`, and
    checks the cwd of a search with no path. On top of pf_wide_scan_paths (which reads "." and those `cd` forms): a
    heredoc, a substitution, a quote the guard's own tokenizer cannot close, any token still holding a quote (the text
    of a nested shell, which the guard parses again) or eight nesting words run the guard. A backslash anywhere already
    runs it (escapes_in_input), so line continuations never reach here."""
    command = _jq_field(payload, "command")
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
    return os.path.lexists(os.environ.get("HOME", "") + "/.jev/ALERT")


def _prettier_config_near(start):
    """True if a prettier config may exist in start or any parent (a superset of prettier's own search)."""
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
    target = _jq_field(payload, "file_path")
    if target is None:
        return True
    if not target or not re.search(r"\.(ts|tsx|js|jsx|json|css|md)$", target):
        return False
    directory = os.path.dirname(target) or "."
    if not os.path.isabs(directory) and os.environ.get("CDPATH"):
        return True
    directory = os.path.abspath(directory)
    if not os.path.isdir(directory):
        return False  # `cd` fails, so the hook does nothing
    return _prettier_config_near(directory)


# Script guards: basename of the resolved script -> (sha256 of the reviewed bytes, or a tuple of them, prefilter).
SCRIPT_PREFILTERS = {
    "agent-lb-bootout-guard.sh": ("8f4a0eea5f7639e7d0f6e8d02a048ffd00db3c37daa9b6f4acb86d612a36d304", pf_bootout),
    "display-wake.sh": ("e701783e043516bdca4397ae0a1acdb8838645ee125931921e345704ce64056b", pf_display_wake),
    # fab6ad8: before the simplify lane's 2026-10-08 edit; b169f02: that edit adds an early allow for plain
    # echo/printf and turns the FIFO grep refusal into a `-D skip` rewrite inside the same FIFO branch, so the
    # prefilter's FIFO clause still covers every input it blocks or rewrites.
    # 557b21d: agent-lb d402b454 (2026-10-08), search tools parsed per segment with paths resolved from the cwd and
    # any `cd`; 7a518f4: bb83dc35, parse failures refused, `watch`, `pushd`, a path-less search's cwd.
    # pf_wide_scan_parse covers each version. Re-pin on every change (test_the_pinned_wide_scan_guard_...).
    "wide-scan-guard.sh": (("fab6ad8cdc002a48698f0fe9bd19e6232492e67ce646f5873e54a7681ef270ac",
                            "b169f02eb85ed225fcebaef814e1c7ad0fccda755b1a21a7887a3f6c8df4bc44",
                            "557b21dbfc1291d0fb7454ff8ddaac823c40ef11636a2451f62192e4e9d35985",
                            "7a518f44edf83c06f5e1565f6e5fe0cb43f5e453ec459837a4b7d1cd02d2aad2"), pf_wide_scan_parse),
    "link-cli-guard.sh": ("cb71baf54200c8dfcd06ba98bb269cb954ed82d11a32ad63a0394d576482ef22", pf_link_cli),
    # 1106066: before factory f76e54a5a; 02f4782: its PC rule and one-shot unblock exception; 3464abb: factory
    # 31b899775, after its review fixes; 64cf185: factory a6f53ea1e, destination parsed by shlex; 77520a3: factory
    # 03b60384a, shell flag clusters, redirections, keywords and live heredoc bodies; 67708a8: factory 61b9e24f5,
    # quoted operators stay words; 6dab221: factory ce1cb753f, stand-in characters fail closed (all 2026-10-07).
    "no-chrome-guard.sh": (("110606670fe66ed2f9dc5824e6d9cb4ed0819286d4b2f73f99b97a73196d9468",
                            "02f4782aadb16febacc1544cfc84dc270d28350fae29c2ce77facdd511b68b82",
                            "3464abbaaf2e8c8ed5ce6fa6203dc51745aad9bb17e220580b442026356bbeee",
                            "64cf185450a08b3faab701fb8c289d32b55611ebe6049065854b2c92fd2450c8",
                            "77520a3185bc8d146aa0ae2a3cf2d0b0267240db5820448fde3487c085c5eb48",
                            "67708a8bbd00fa5fed75e70422ecd1281855606d32bb60c811e78f152e0ec75b",
                            "6dab221f51c25d4c97645ffe8e34318426fa9682e4a7db6ce77fc45a3178ea84"), pf_no_chrome),
    "railway-vars-guard.sh": ("9fa0e824554830e501bda0d9cb8a21213e6011935101b26f25e03a99796818dd", pf_railway),
    "route.sh": ("7735c06fbe7264f0d92403e0bd7b18920f9457196493e9bce9b787e53404ed97", pf_jev_alert),
}
# Inline commands: sha256 of the exact command string -> prefilter.
COMMAND_PREFILTERS = {
    # bash -c 'CMD=$(cat | jq -r ".tool_input.command // empty"); ... rm\s+-rf\s+/|DROP\s+(DATABASE|TABLE) ...'
    "0fbf6582d067534e0cf7179a1801b12c675c75397170a48d36aec72171483e7c": pf_dangerous,
    # bash -c 'FILE=$(cat | jq -r ".tool_input.file_path // empty"); ... npx prettier --find-config-path ...'
    "55c67a61ea18f3d6d58b572fe09f1699073e09ddbbc05b2d15ccf853937501d7": pf_prettier,
}


# ---------------------------------------------------------------------------------------------------- planning


class Plan(object):
    __slots__ = ("hook", "command", "timeout", "kind", "script", "argv", "interp", "devnull", "fallback_true",
                 "fallback_text", "source", "reason", "prefilter", "floor", "bare")

    def __init__(self, hook):
        self.hook = hook
        self.command = hook.get("command", "") if isinstance(hook.get("command"), str) else ""
        timeout = hook.get("timeout")
        self.timeout = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else DEFAULT_TIMEOUT
        self.kind, self.script, self.argv, self.interp = "external", None, None, None
        self.devnull, self.fallback_true, self.fallback_text, self.source = False, False, None, None
        self.reason, self.prefilter = "", None
        self.floor = floor_name(self.command)  # the floor guard this hook runs, or None
        self.bare = None  # a floor guard's own command without its shell wrapper (run bare, wrapper applied here)


def floor_name(command):
    if not command:
        return None
    named = FLOOR_COMMANDS.get(hashlib.sha256(command.encode("utf-8")).hexdigest())
    if named:
        return named
    match = FLOOR_WORD.search(command)
    if match:
        return match.group(1)
    return "dangerous-command" if FLOOR_TEXT in command else None


def sibling_sources(script_dir, source, depth=0, seen=None):
    """Source of modules a script imports from its own directory (they run in-process too)."""
    seen = seen if seen is not None else set()
    found = []
    for match in IMPORT_NAMES.finditer(source):
        names = [match.group(1)] if match.group(1) else [n.strip() for n in match.group(2).split(",")]
        for name in names:
            top = name.split(".")[0]
            if top in seen:
                continue
            for candidate in (os.path.join(script_dir, top + ".py"), os.path.join(script_dir, top, "__init__.py")):
                if os.path.isfile(candidate):
                    seen.add(top)
                    try:
                        with open(candidate, encoding="utf-8") as handle:
                            text = handle.read()
                    except (OSError, UnicodeDecodeError):
                        text = "os._exit  # unreadable"
                    found.append(text)
                    if depth < 2:
                        found.extend(sibling_sources(os.path.dirname(candidate), text, depth + 1, seen))
                    break
    return found


def hazardous(text):
    for match in HAZARD.finditer(text):
        if match.group(0) == "os.dup2" and DUP2_STD.match(text, match.start()):
            continue
        return True
    return False


def plan_hook(hook, inproc_names):
    plan = Plan(hook)
    command = plan.command
    if hook.get("type", "command") != "command" or not command:
        plan.reason = "not a command hook"
        return plan
    if command.strip() in REWRITERS:
        plan.kind, plan.reason = "rewriter", "side-effect free rewriter"
        return plan
    prefilter = COMMAND_PREFILTERS.get(hashlib.sha256(command.encode("utf-8")).hexdigest())
    if prefilter is not None:
        sensitive = env_sensitive()
        if sensitive:
            plan.reason = "pinned command, but %s is set" % sensitive
        else:
            plan.kind, plan.prefilter, plan.reason = "filtered", prefilter, "pinned command"
        return plan
    match = SHAPE.match(command)
    if not match:
        plan.reason = "shape"
        tail = WRAPPER_TAIL.search(command) if plan.floor else None
        if tail and (tail.group("devnull") or tail.group("true") or tail.group("fb") is not None) \
                and command[:tail.start()].strip():
            plan.devnull = bool(tail.group("devnull"))
            plan.fallback_true = bool(tail.group("true"))
            plan.fallback_text = tail.group("fb")
            plan.bare = command[:tail.start()]
        return plan
    plan.devnull = bool(match.group("devnull"))
    plan.fallback_true = bool(match.group("true"))
    plan.fallback_text = match.group("fb")
    if plan.floor and (plan.devnull or plan.fallback_true or plan.fallback_text is not None):
        # Run the guard bare and apply its wrapper here, so its own failure is seen before the wrapper hides it.
        plan.bare = command[:match.end("args")]
    if match.group("interp") is None:
        # A pinned shell guard, bare or inside `2>/dev/null || true`: when its prefilter says it cannot act, the
        # wrapped command ends with exit 0 and no output either way. A printf fallback answers whenever the guard
        # fails for a reason no prefilter sees (startup, a missing tool), so that guard always runs.
        pinned = SCRIPT_PREFILTERS.get(os.path.basename(os.path.realpath(expand_word(match.group("script")))))
        if pinned is not None:
            sensitive = env_sensitive()
            if match.group("fb") is not None:
                plan.reason = "pinned script with a fallback answer"
            elif sensitive:
                plan.reason = "pinned script, but %s is set" % sensitive
            elif sha256_file(os.path.realpath(expand_word(match.group("script")))) in (
                    pinned[0] if isinstance(pinned[0], tuple) else (pinned[0],)):
                plan.kind, plan.prefilter, plan.reason = "filtered", pinned[1], "pinned script"
            else:
                plan.reason = "script changed since its prefilter was reviewed"
            return plan
    script = expand_word(match.group("script"))
    if os.path.basename(os.path.realpath(script)) not in inproc_names:
        plan.reason = "not on the in-process list"
        return plan
    if not os.path.isfile(script):
        plan.reason = "missing script"
        return plan
    interp = match.group("interp")
    if interp is None:
        interp = interpreter_of(script)
    if not python_like(interp):
        plan.reason = "not python"
        return plan
    if not utf8_locale() and not same_interpreter(interp):
        plan.reason = "locale"
        return plan
    try:
        with open(script, "rb") as handle:
            data = handle.read()
        source = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        plan.reason = "unreadable"
        return plan
    pinned = inproc_names.get(os.path.basename(os.path.realpath(script))) if isinstance(inproc_names, dict) else None
    if pinned and hashlib.sha256(data).hexdigest() != pinned:
        plan.reason = "changed since the parity fixture passed on it"
        return plan
    texts = [source] + sibling_sources(os.path.dirname(os.path.realpath(script)), source)
    if any(hazardous(text) for text in texts):
        plan.reason = "hazard"
        return plan
    plan.script, plan.interp, plan.source = script, interp, source
    plan.argv = [script] + match.group("args").split()
    plan.kind = "inproc"
    plan.reason = "same interpreter" if same_interpreter(interp) else "other interpreter"
    return plan


# ---------------------------------------------------------------------------------------------------- execution

_SPAWNS = [0]


def _audit(event, _args):
    if event in ("subprocess.Popen", "os.posix_spawn", "os.fork", "os.forkpty", "os.system", "os.spawn",
                 "os.startfile"):
        _SPAWNS[0] += 1


class Payload(object):
    """The hook input as one named temp file; every reader (fd 0, a child) opens it afresh at byte 0."""

    def __init__(self, raw):
        self.raw = raw
        self.path = None
        self.restore()

    def restore(self):
        """(Re)create the file. A write that fails (ENOSPC) removes the partial file and raises."""
        fd, path = tempfile.mkstemp(prefix="hook-dispatch-")
        try:
            raw = self.raw
            while raw:
                raw = raw[os.write(fd, raw):]
        except BaseException:
            os.close(fd)
            os.unlink(path)
            raise
        os.close(fd)
        self.path = path

    def open_fd(self):
        return os.open(self.path, os.O_RDONLY)

    def close(self):
        if self.path is None:
            return
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self.path = None


def _stdlib_dirs():
    dirs = set()
    for prefix in {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}:
        dirs.add(os.path.join(prefix, "lib"))
        dirs.add(os.path.join(prefix, "Library"))
    return tuple(dirs)


def run_inproc(plan, payload):
    """Execute a Python guard inside this process, as `<interp> <script> <args>` would run on its own."""
    saved_fds = [os.dup(0), os.dup(1), os.dup(2)]
    saved_std = (sys.stdin, sys.stdout, sys.stderr)
    saved_argv, saved_path = sys.argv[:], sys.path[:]
    saved_main = sys.modules.get("__main__")
    saved_modules = set(sys.modules)
    saved_environ = dict(os.environ)
    saved_cwd = os.getcwd()
    try:
        out_file, err_file = tempfile.TemporaryFile(), tempfile.TemporaryFile()
        in_fd = payload.open_fd()
        os.dup2(in_fd, 0)
        os.close(in_fd)
        os.dup2(out_file.fileno(), 1)
        os.dup2(err_file.fileno(), 2)
    except BaseException:
        # Setup failed before the guard ran: put fds 0-2 back so the answer still reaches Claude Code.
        for target, fd in enumerate(saved_fds):
            os.dup2(fd, target)
            os.close(fd)
        raise
    enc_in = getattr(saved_std[0], "encoding", None) or "utf-8"
    err_in = getattr(saved_std[0], "errors", None) or "strict"
    enc_out = getattr(saved_std[1], "encoding", None) or "utf-8"
    err_out = getattr(saved_std[1], "errors", None) or "strict"
    enc_err = getattr(saved_std[2], "encoding", None) or "utf-8"
    sys.stdin = io.TextIOWrapper(io.BufferedReader(io.FileIO(0, "r", closefd=False)), encoding=enc_in, errors=err_in)
    sys.stdout = io.TextIOWrapper(io.BufferedWriter(io.FileIO(1, "w", closefd=False)), encoding=enc_out,
                                  errors=err_out)
    sys.stderr = io.TextIOWrapper(io.BufferedWriter(io.FileIO(2, "w", closefd=False)), encoding=enc_err,
                                  errors="backslashreplace", line_buffering=True)
    module = types.ModuleType("__main__")
    module.__file__ = plan.script
    module.__builtins__ = builtins
    module.__cached__ = None
    module.__spec__ = None
    module.__package__ = None
    sys.modules["__main__"] = module
    sys.argv = list(plan.argv)
    sys.path[0:1] = [os.path.dirname(os.path.realpath(plan.script))]
    code, timed_out, crashed = 0, False, False
    spawned_before = _SPAWNS[0]

    def on_alarm(_signum, _frame):
        raise _GuardTimeout()

    previous = signal.signal(signal.SIGALRM, on_alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, plan.timeout)
        try:
            # Compiled here, not cached: compile-time warnings (SyntaxWarning) reach the guard's stderr as they would
            # when its interpreter compiles the script.
            exec(compile(plan.source, plan.script, "exec", dont_inherit=True), module.__dict__)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except _GuardTimeout:
        timed_out = True
    except SystemExit as stop:
        value = stop.code
        if value is None:
            code = 0
        elif isinstance(value, int):
            code = value & 0xFF
        else:
            try:
                sys.stderr.write(str(value) + "\n")
            except Exception:
                pass
            code = 1
    except BaseException:
        crashed = True
        code = 1
        try:
            import traceback
            kind, value, tb = sys.exc_info()
            traceback.print_exception(kind, value, tb.tb_next if tb is not None else None, file=sys.stderr)
        except Exception:
            pass
    finally:
        signal.signal(signal.SIGALRM, previous)
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        sys.stdin, sys.stdout, sys.stderr = saved_std
        for target, fd in enumerate(saved_fds):
            os.dup2(fd, target)
            os.close(fd)
        sys.argv, sys.path[:] = saved_argv, saved_path
        if saved_main is not None:
            sys.modules["__main__"] = saved_main
        stdlib = _stdlib_dirs()
        for name in set(sys.modules) - saved_modules:
            origin = getattr(sys.modules.get(name), "__file__", None) or ""
            if not origin.startswith(stdlib):
                sys.modules.pop(name, None)
        if dict(os.environ) != saved_environ:
            os.environ.clear()
            os.environ.update(saved_environ)
        try:
            if os.getcwd() != saved_cwd:
                os.chdir(saved_cwd)
        except OSError:
            os.chdir(saved_cwd)
    out_file.seek(0)
    err_file.seek(0)
    result = Result(code, out_file.read(), err_file.read(), timed_out, "inproc", _SPAWNS[0] - spawned_before)
    out_file.close()
    err_file.close()
    if crashed:
        result.mode = "inproc-crash"
    return result, crashed


def apply_wrapper(plan, result):
    """What the shell around the guard makes of its result: `2>/dev/null`, `|| true`, `|| { printf %s '..'; }`."""
    if result.timed_out:
        return result  # Claude Code kills the whole shell; the fallback never runs.
    if plan.devnull:
        result.err = b""
    if result.code != 0:
        if plan.fallback_true:
            result.code = 0
        elif plan.fallback_text is not None:
            result.out += plan.fallback_text.encode("utf-8")
            result.code = 0
    return result


_DECISIONS = {"allow", "ask", "deny"}


def answer_problem(out):
    """Why a hook's stdout is not a hook answer Claude Code reads cleanly, or None (nothing, or a well-formed one)."""
    if not out.strip():
        return None
    obj = parse_json(out)
    if obj is None:
        return "printed output that is not a JSON hook answer"
    specific = obj.get("hookSpecificOutput")
    if specific is not None:
        if not isinstance(specific, dict):
            return "printed a malformed hook answer (hookSpecificOutput is not an object)"
        decision = specific.get("permissionDecision", "allow")
        if not isinstance(decision, str) or decision not in _DECISIONS:
            return "printed a malformed hook answer (permissionDecision %s)" % json.dumps(decision)
        reason = specific.get("permissionDecisionReason")
        if reason is not None and not isinstance(reason, str):
            return "printed a malformed hook answer (permissionDecisionReason is not text)"
    if "decision" in obj and obj["decision"] not in ("block", "approve"):
        return "printed a malformed hook answer (decision %s)" % json.dumps(obj["decision"])
    return None


def floor_failure(result):
    """How a floor guard failed (before any shell wrapper), or None when it answered: allow, deny or block."""
    if result.timed_out:
        return "timed out"
    if result.code == 2:
        return None  # a block
    if result.code == 127:
        return "is missing (exit 127)"
    if result.code != 0:
        return "crashed (exit %d)" % result.code
    return answer_problem(result.out)


def finish(plan, raw):
    """The guard's result as Claude Code would see it; for a floor guard, its failure is read first."""
    if plan.floor:
        raw.floor_failure = floor_failure(raw)
        if raw.code == 2 and not raw.timed_out:
            return raw  # a floor guard's block stands, whatever its wrapper would make of exit 2
    if plan.kind == "inproc" or plan.bare:
        return apply_wrapper(plan, raw)
    return raw


class External(object):
    """A hook in its own process, through /bin/sh -c, as Claude Code runs it; drained by a reader thread."""

    def __init__(self, plan, payload):
        import subprocess
        import threading
        self.plan, self.started = plan, time.monotonic()
        self.outcome = None
        argv, executable, env = [SHELL, "-c", plan.bare or plan.command], None, None
        if plan.kind == "rewriter":
            # The rewriter is found on this process's own PATH, as /bin/sh would per-hook, before rewriter_env puts
            # git's directory first (2026-10-08 review: another rtk in that directory would have run instead). The
            # same argv as `/bin/sh -c 'rtk hook claude'` hands it; not on PATH, the shell reports it as before.
            words = plan.command.split()
            target = which(words[0])
            if target:
                argv, executable = words, target
            env = rewriter_env()
        stdin = payload.open_fd()
        try:
            self.proc = subprocess.Popen(argv, executable=executable, stdin=stdin, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True, env=env)
        finally:
            os.close(stdin)
        self.thread = threading.Thread(target=self._drain)
        self.thread.daemon = True
        self.thread.start()

    def _drain(self):
        import subprocess
        try:
            out, err = self.proc.communicate(timeout=self.plan.timeout)
            code = self.proc.returncode
            self.outcome = Result(code if code >= 0 else 128 - code, out, err, False, "external", 1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                self.proc.communicate(timeout=5)
            except Exception:
                pass
            self.outcome = Result(1, b"", b"", True, "external", 1)
        except Exception as exc:  # the dispatcher lost the guard's answer: refuse, as the guard might have
            self.outcome = Result(2, b"", ("hook-dispatch: could not read this guard's answer (%s); refused\n"
                                           % exc.__class__.__name__).encode(), False, "external", 1)

    def result(self):
        self.thread.join()
        return self.outcome


# ---------------------------------------------------------------------------------------------------- merging

_RANK = {"allow": 1, "ask": 2, "deny": 3}


def parse_json(out):
    text = out.decode("utf-8", "replace").strip()
    if not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def text_of(value):
    """A guard's free-text field as text, whatever JSON type it sent."""
    return value if isinstance(value, str) else json.dumps(value)


def deny_reason(obj):
    if not obj:
        return None
    specific = obj.get("hookSpecificOutput") if isinstance(obj.get("hookSpecificOutput"), dict) else {}
    if specific.get("permissionDecision") == "deny":
        reason = specific.get("permissionDecisionReason") or obj.get("reason")
        return text_of(reason) if reason else "Blocked by hook"
    if obj.get("decision") == "block":
        return text_of(obj["reason"]) if obj.get("reason") else "Blocked by hook"
    return None


def block_message(plan, result, obj):
    """The text Claude Code shows for a blocking hook: its JSON block reason, else `[<command>]: <stderr>`."""
    reason = deny_reason(obj)
    if reason:
        return reason
    return "[%s]: %s" % (plan.command, result.err.decode("utf-8", "replace") or "No stderr output")


def encode(obj):
    # ASCII escapes: a lone surrogate in a guard's text cannot break the answer.
    return (json.dumps(obj, ensure_ascii=True) + "\n").encode("ascii")


def refusal(event, note, blocked=False):
    """The dispatcher's own failure. PreToolUse (or a guard that did block): exit 2 with the reason as the JSON block
    Claude Code reads for this event, and on stderr. Other events: a non-blocking notice (exit 1, the note on
    stderr), as a failed guard gave per-hook."""
    err = (note.rstrip("\n") + "\n").encode("utf-8", "backslashreplace")
    if event == "PreToolUse":
        obj = {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                                      "permissionDecisionReason": note}}
    elif blocked:
        obj = {"decision": "block", "reason": note}
    else:
        return Result(1, b"", err, mode="refused")
    return Result(2, encode(obj), err, mode="refused")


def notice_text(result):
    """The non-blocking error notice Claude Code (2.1.293) shows for a guard that failed without JSON:
    `Failed with non-blocking status code: <stderr, trimmed>`, or `No stderr output` in its place."""
    return NOTICE_PREFIX + (result.err.decode("utf-8", "replace").strip() or "No stderr output")


def refuse_failed_floor(plan, result):
    """A floor guard that failed answers as a block of its own, with the reason in its message."""
    if result is None or not result.floor_failure:
        return result
    note = "hook-dispatch: floor guard %s %s, so this call is refused (a floor guard that fails denies)" % (
        plan.floor, result.floor_failure)
    detail = result.err.decode("utf-8", "replace").strip()[-500:]
    return Result(2, b"", (note + ("\n" + detail if detail else "") + "\n").encode("utf-8", "backslashreplace"),
                  False, "floor-refused", result.spawned)


def merge(event, plans, results):
    """merge_answers, failing closed: answers it cannot merge deny, with every block message it could read."""
    try:
        return merge_answers(event, plans, results)
    except Exception as exc:
        messages = []
        for plan, result in zip(plans, results):
            if result is None or result.timed_out:
                continue
            try:
                obj = parse_json(result.out) if result.out else None
                if result.code == 2 or deny_reason(obj):
                    messages.append(block_message(plan, result, obj))
            except Exception:
                messages.append("[%s]: answer unreadable" % plan.command)
        blocked = bool(messages)
        if not messages:
            messages.append("hook-dispatch: could not merge the guards' answers (%s); refused"
                            % exc.__class__.__name__)
        return refusal(event, "\n".join(messages), blocked)


def merge_answers(event, plans, results):
    """One answer for Claude Code from the results of every guard of the entry, in config order.

    Claude Code (2.1.293) reads a hook's stdout as JSON whatever its exit code; a JSON deny or `decision: block`
    blocks with its reason, and an exit 2 without one blocks with `[<command>]: <stderr>`. The dispatcher answers
    with that same text as a JSON block reason, so the message keeps the guard's own command, not the dispatcher's.
    """
    if event == "PreToolUse":
        results = [refuse_failed_floor(p, r) for p, r in zip(plans, results)]
    pairs = [(p, r) for p, r in zip(plans, results) if r is not None and not r.empty()]
    pairs = [(p, r) for p, r in pairs if not r.timed_out]  # a cancelled hook has no effect
    if not pairs:
        return Result(0, b"", b"")
    parsed = [(p, r, parse_json(r.out) if r.out else None) for p, r in pairs]
    blocks = [(p, r, obj) for p, r, obj in parsed if r.code == 2 or deny_reason(obj)]
    if len(pairs) == 1 and not (blocks and blocks[0][1].code == 2 and not deny_reason(blocks[0][2])):
        _p, only, _obj = parsed[0]
        return Result(only.code, only.out, only.err)  # byte for byte
    # Config order: JSON answers, and the notice of each guard that failed without one (Claude Code ignores stderr
    # once stdout is JSON, so a merged JSON answer carries those notices in its systemMessage).
    items = [obj if obj is not None else notice_text(r) for _p, r, obj in parsed
             if obj is not None or r.code not in (0, 2)]
    objects = [item for item in items if isinstance(item, dict)]
    notices = [item for item in items if not isinstance(item, dict)]
    plain = [r.out for _p, r, obj in parsed if obj is None and r.code == 0 and r.out]
    errors = [r for _p, r, obj in parsed if obj is None and r.code not in (0, 2)]
    if blocks:
        messages = [block_message(p, r, obj) for p, r, obj in blocks]
        merged = merge_objects(event, items, plain) if items or plain else {}
        merged.pop("decision", None)
        merged.pop("reason", None)
        if event == "PreToolUse":
            specific = dict(merged.get("hookSpecificOutput") or {}, hookEventName=event)
            specific.pop("updatedInput", None)  # the call is denied; a rewrite of it has no effect
            specific["permissionDecision"] = "deny"
            specific["permissionDecisionReason"] = "\n".join(messages)
            merged["hookSpecificOutput"] = specific
        else:
            merged["decision"] = "block"
            merged["reason"] = "\n".join(messages)
        return Result(2, encode(merged), b"".join(r.err for _p, r, _obj in blocks))
    # Without a JSON answer Claude Code puts its prefix before this process's stderr itself: the first notice goes
    # without it, each later one keeps its own, so the user reads one `Failed with ...` line per failed guard.
    notice_err = ("\n".join(notices)[len(NOTICE_PREFIX):] + "\n").encode("utf-8", "backslashreplace") \
        if notices else b""
    if not objects and not plain:
        return Result(errors[0].code, b"", notice_err)
    if not objects and (not errors or event != "UserPromptSubmit"):
        # Plain stdout reaches only the transcript here, as it does from a failed guard; the notices stay notices.
        return Result(errors[0].code if errors else 0, b"".join(plain), notice_err)
    # JSON answers, or UserPromptSubmit context (plain stdout) beside a failed guard: one JSON answer.
    return Result(0, encode(merge_objects(event, items, plain)), b"")


def merge_objects(event, objects, plain):
    """Merge JSON answers in config order. A str among them is a failed guard's notice: it joins systemMessage."""
    merged, specific = {}, {}
    contexts, reasons_by_rank, system = [], {}, []
    decision, decision_reasons, stop_reasons = None, [], []
    for obj in objects:
        if not isinstance(obj, dict):
            system.append(obj)
            continue
        for key, value in obj.items():
            if key == "hookSpecificOutput" and isinstance(value, dict):
                for skey, svalue in value.items():
                    if skey == "additionalContext":
                        if svalue not in (None, ""):
                            contexts.append(str(svalue))
                    elif skey == "permissionDecision":
                        rank = _RANK.get(svalue, 0) if isinstance(svalue, str) else 0
                        reasons_by_rank.setdefault(rank, [])
                        if value.get("permissionDecisionReason"):
                            reasons_by_rank[rank].append(text_of(value["permissionDecisionReason"]))
                        if rank > _RANK.get(specific.get("permissionDecision"), 0):
                            specific["permissionDecision"] = svalue
                    elif skey in ("permissionDecisionReason", "hookEventName"):
                        continue
                    elif skey == "updatedInput":
                        specific["updatedInput"] = svalue  # the last in config order wins
                    elif skey not in specific:
                        specific[skey] = svalue
            elif key == "continue":
                merged["continue"] = merged.get("continue", True) and bool(value)
                if value is False and obj.get("stopReason"):
                    stop_reasons.append(str(obj["stopReason"]))
            elif key == "stopReason":
                continue
            elif key == "systemMessage":
                if value:
                    system.append(str(value))
            elif key == "decision":
                if value == "block":
                    decision = "block"
                    if obj.get("reason"):
                        decision_reasons.append(text_of(obj["reason"]))
                elif decision is None:
                    decision = value
            elif key == "reason":
                continue
            elif key == "suppressOutput":
                merged["suppressOutput"] = merged.get("suppressOutput", False) or bool(value)
            elif key not in merged:
                merged[key] = value
    if event == "UserPromptSubmit":
        contexts.extend(p.decode("utf-8", "replace").rstrip("\n") for p in plain)
    if "permissionDecision" in specific:
        reasons = reasons_by_rank.get(_RANK.get(specific["permissionDecision"], 0)) or []
        if reasons:
            specific["permissionDecisionReason"] = "\n".join(reasons)
    if contexts:
        specific["additionalContext"] = "\n".join(contexts)
    if specific:
        merged["hookSpecificOutput"] = dict([("hookEventName", event)] + list(specific.items()))
    if stop_reasons:
        merged["stopReason"] = "\n".join(stop_reasons)
    if system:
        merged["systemMessage"] = "\n".join(system)
    if decision is not None:
        merged["decision"] = decision
        if decision_reasons:
            merged["reason"] = "\n".join(decision_reasons)
    return merged


# ---------------------------------------------------------------------------------------------------- the entry


def registry_candidates(rev=None):
    """Where this settings entry's registry may be, best first (see Registry revisions above)."""
    base = registry_path()
    folder = os.path.dirname(base)
    pinned = bool(os.environ.get("HOOK_DISPATCH_REGISTRY"))
    paths = []
    if rev and not pinned:
        paths.append(os.path.join(folder, "registry.%s.json" % rev))
    paths += [base, base + ".bak"]
    if not rev and not pinned:
        paths.append(os.path.join(folder, "registry.legacy.json"))
    return paths


def load_entry(event, key, rev=None):
    """(registry, hooks, path) from the first candidate of this entry's own fold whose entry for (event, key) is a
    valid hook list. A damaged or missing entry in one copy falls through to the next copy of the same fold; a copy
    of another fold never answers (2026-10-07 review M1: a session on rev R1 whose file is gone must not run the
    guards of R2, which may lack R1's deny). An entry with a rev reads only registries carrying that rev; one
    without a rev reads only registries without one (the fold before revs: registry.legacy.json, or a registry.json
    still from then). Raises with every reason when none has it."""
    errors = []
    for path in registry_candidates(rev):
        try:
            with open(path, encoding="utf-8") as handle:
                registry = json.load(handle)
        except (OSError, ValueError) as exc:
            errors.append("%s: %s" % (os.path.basename(path), exc.__class__.__name__))
            continue
        if not (isinstance(registry, dict) and isinstance(registry.get("entries"), dict)):
            errors.append("%s: not a registry" % os.path.basename(path))
            continue
        if (registry.get("rev") or None) != rev:
            errors.append("%s: fold %s, not this entry's %s" % (
                os.path.basename(path), registry.get("rev") or "without a rev", rev or "(without a rev)"))
            continue
        if rev and entries_rev(registry["entries"]) != rev:
            # The rev names the hooks the fold runs (install-policy registry_rev); entries edited since, a guard
            # dropped from a list included, no longer hash to it (2026-10-08 review M1).
            errors.append("%s: entries do not hash to rev %s" % (os.path.basename(path), rev))
            continue
        try:
            return registry, entry_hooks(registry, event, key), path
        except ValueError as exc:
            errors.append("%s: %s" % (os.path.basename(path), exc))
    raise ValueError("; ".join(errors))


def entries_rev(entries):
    """install-policy.py registry_rev: the first 12 hex of sha256 over the entries as sorted-key JSON."""
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def entry_hooks(registry, event, key):
    """The entry's hooks, deduplicated. Raises unless it is a non-empty list of command hooks: install-policy never
    writes anything else, so anything else is damage, and skipping it would skip guards."""
    hooks = (registry["entries"].get(event) or {}) if isinstance(registry["entries"].get(event), dict) else None
    if hooks is None or key not in hooks:
        raise ValueError("no %s '%s' entry" % (event, key))  # settings and registry disagree: half installed
    hooks = hooks[key]
    if not isinstance(hooks, list) or not hooks:
        raise ValueError("the %s '%s' entry is %s, not a list of hooks" % (event, key, type(hooks).__name__))
    for hook in hooks:
        if not (isinstance(hook, dict) and hook.get("type", "command") == "command"
                and isinstance(hook.get("command"), str) and hook["command"].strip()):
            raise ValueError("the %s '%s' entry holds %r, not a command hook" % (event, key, hook))
    seen, kept = set(), []
    for hook in hooks:
        marker = json.dumps([hook.get("type"), hook.get("command")])
        if marker in seen:
            continue
        seen.add(marker)
        kept.append(hook)
    return kept


def trace(record):
    path = os.environ.get("HOOK_DISPATCH_TRACE")
    if not path:
        return
    try:
        with open(path, "a") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def trace_hooks(plans, results):
    rows = []
    for plan, result in zip(plans, results):
        row = {"command": plan.command, "kind": plan.kind, "reason": plan.reason}
        if result is not None:
            row.update(mode=result.mode, code=result.code, timed_out=result.timed_out, spawned=result.spawned,
                       out=result.out.decode("utf-8", "replace"), err=result.err.decode("utf-8", "replace"))
        rows.append(row)
    return rows


def rewriter_env():
    """The rewriter's environment: PATH with git's own directory first. Outside a project with a .claude directory
    `rtk hook claude` runs `git rev-parse --show-toplevel` (its only child, checked 2026-10-07 by shimming every name
    on PATH) and forks once per PATH entry ahead of git's directory: 24 processes per Bash call on Studio's PATH.
    The same git binary is found either way, so its answer is the same."""
    env = dict(os.environ)
    git = which("git")
    if git:
        folder = os.path.dirname(git)
        path = env.get("PATH", os.defpath)
        if path.split(os.pathsep)[0] != folder:
            env["PATH"] = folder + os.pathsep + path
    return env


def exec_into(plan, payload, record):
    """Replace this process with the rewriter: same pid, bounded by the rewriter's own timeout. The timer is armed
    just before the exec with SIGALRM at its default action, and an interval timer survives exec, so a rewriter that
    hangs or answers after its timeout is killed then and its late rewrite never lands. Without it, Claude Code's
    timeout for the whole entry (every guard's timeout added up, plus 5 s) was the bound, and a rewrite that came
    after the rewriter's own timeout but before the entry's was applied, which per-hook it never was. Build lead
    decision (a), 2026-10-08: the kill shows as a failed-hook notice (`Failed with non-blocking status code: No
    stderr output`) where per-hook the cancel showed nothing; that notice is accepted and replaces the 2026-10-07
    review M2 choice of no timer. The payload file is unlinked first (fd 0 keeps it readable): a successful exec
    leaves nothing behind. When the exec fails, the timer is cleared, the file is written again and the caller runs
    the rewriter through /bin/sh."""
    words = plan.command.split()
    target = which(words[0])
    if not target:
        return  # not on PATH: let the caller run it through /bin/sh, which reports it as before
    fd = payload.open_fd()
    record["exec"] = plan.command
    trace(record)
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(fd, 0)
    os.close(fd)
    payload.close()
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
    signal.setitimer(signal.ITIMER_REAL, plan.timeout)
    try:
        os.execve(target, words, rewriter_env())
    except OSError:
        signal.setitimer(signal.ITIMER_REAL, 0)
        payload.restore()


def updates_input(result):
    obj = parse_json(result.out) if result is not None and result.code == 0 and result.out else None
    specific = obj.get("hookSpecificOutput") if obj and isinstance(obj.get("hookSpecificOutput"), dict) else {}
    return "updatedInput" in specific


def subagent_bash(payload, event):
    """Only the hook payload distinguishes a Claude subagent from its lead."""
    return (event == "PreToolUse" and isinstance(payload, dict) and "agent_id" in payload
            and payload.get("tool_name") == "Bash")


def suppress_subagent_owner(result, payload, event):
    """Compose after other rewrites; preserve denials and explicit seat-run --owner."""
    if not subagent_bash(payload, event) or result.code != 0:
        return result
    obj = parse_json(result.out) if result.out else {}
    if obj is None or deny_reason(obj):
        return result
    specific = obj.setdefault("hookSpecificOutput", {})
    original = payload.get("tool_input")
    updated = specific.get("updatedInput", original)
    if not isinstance(updated, dict) or not isinstance(updated.get("command"), str):
        return result
    prefix = "export FACTORY_OWNER_PANE=none;"
    command = updated["command"]
    if not command.startswith(prefix):
        command = prefix + " " + command
    specific["hookEventName"] = event
    specific["updatedInput"] = dict(updated, command=command)
    return Result(result.code, encode(obj), result.err)


def log_failures(event, key, plans, results):
    """One line per guard that failed (timeout, crash, missing, malformed) in dispatch/failures.jsonl; a floor guard's
    failure refused the call, any other guard's failed open. Never raises; the file is rotated past 1 MB."""
    rows = []
    for plan, result in zip(plans, results):
        if result is None or result.mode.startswith("skipped"):
            continue
        failure = result.floor_failure if plan.floor else (
            "timed out" if result.timed_out else
            ("exited %d" % result.code if result.code not in (0, 2) else None))
        if failure:
            rows.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, "key": key,
                         "command": plan.command[:300], "floor": plan.floor, "failure": failure,
                         "outcome": "refused" if plan.floor and event == "PreToolUse" else "failed open"})
    if not rows:
        return
    try:
        path = os.path.join(HERE, "dispatch", "failures.jsonl")
        if os.path.exists(path) and os.path.getsize(path) > (1 << 20):
            os.replace(path, path + ".1")
        with open(path, "a") as handle:
            handle.write("".join(json.dumps(row) + "\n" for row in rows))
    except (OSError, ValueError):
        pass


def run_entry(event, hooks, raw, inproc_names, key=None):
    record = {"event": event, "pid": os.getpid(), "ppid": os.getppid()}
    plans = []
    for hook in hooks:
        try:
            plans.append(plan_hook(hook, inproc_names))
        except Exception as exc:  # planning trouble keeps the old path
            plan = Plan(hook)
            plan.reason = "plan error %s" % exc.__class__.__name__
            plans.append(plan)
    try:
        payload_obj = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        payload_obj = None
    payload = Payload(raw)
    results = [None] * len(plans)
    started = {}
    try:
        for index, plan in enumerate(plans):
            if plan.kind == "filtered":
                try:
                    needed = escapes_in_input(payload_obj) or plan.prefilter(payload_obj)
                except Exception:
                    needed = True
                if needed:
                    plan.kind = "external"
                    plan.reason += ", prefilter matched"
                else:
                    results[index] = Result(0, b"", b"", False, "skipped")
        for index, plan in enumerate(plans):
            if plan.kind == "external":
                started[index] = External(plan, payload)
        for index, plan in enumerate(plans):
            if plan.kind != "inproc":
                continue
            try:
                result, crashed = run_inproc(plan, payload)
            except Exception as exc:  # the dispatcher failed around the guard, before or after it ran
                plan.kind, plan.reason = "external", "in-process setup failed (%s)" % exc.__class__.__name__
                started[index] = External(plan, payload)
                continue
            if crashed and not same_interpreter(plan.interp):
                plan.kind, plan.reason = "external", "crashed under another interpreter"
                started[index] = External(plan, payload)
                continue
            results[index] = finish(plan, result)
        for index, plan in enumerate(plans):
            if plan.kind != "rewriter":
                continue
            known = [(i, r) for i, r in enumerate(results) if r is not None]
            blocked = any(r.code == 2 or deny_reason(parse_json(r.out)) for _i, r in known if not r.timed_out)
            superseded = any(i > index and updates_input(r) for i, r in known)
            if blocked or superseded:
                results[index] = Result(0, b"", b"", False, "skipped (%s)" % ("blocked" if blocked else "superseded"))
                continue
            # A floor guard that failed under a `|| true` wrapper reads as empty here, yet refuses the call at the
            # merge; it never counts as quiet (2026-10-08 review: the exec skipped that refusal).
            others_quiet = not started and all(r.empty() and not r.floor_failure for _i, r in known) and \
                all(p.kind == "rewriter" or results[i] is not None for i, p in enumerate(plans) if i != index)
            if others_quiet and sum(1 for p in plans if p.kind == "rewriter") == 1 \
                    and not subagent_bash(payload_obj, event):
                record["hooks"] = trace_hooks(plans, results)
                record["spawned"] = _SPAWNS[0]
                exec_into(plan, payload, record)
            started[index] = External(plan, payload)
        for index, external in started.items():
            results[index] = finish(plans[index], external.result())
        log_failures(event, key, plans, results)
        record["hooks"] = trace_hooks(plans, results)
        record["spawned"] = _SPAWNS[0]
        answer = suppress_subagent_owner(merge(event, plans, results), payload_obj, event)
        record["answer"] = {"code": answer.code, "out": answer.out.decode("utf-8", "replace"),
                            "err": answer.err.decode("utf-8", "replace")}
        trace(record)
        return answer
    finally:
        payload.close()


def answer(result):
    """Write the answer; return its exit code."""
    out, err = sys.stdout.buffer, sys.stderr.buffer
    if result.out:
        out.write(result.out)
        out.flush()
    if result.err:
        err.write(result.err)
        err.flush()
    return result.code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] not in EVENTS:
        # A settings entry this dispatcher cannot read runs no guards: refuse a tool call, else a notice.
        sys.stderr.write("hook-dispatch: usage: hook-dispatch.py <%s> <matcher> [<rev>]; its guards did not run\n"
                         % "|".join(EVENTS))
        return 2 if argv[:1] == ["PreToolUse"] else 1
    event, key = argv[0], argv[1]
    rev = argv[2] if len(argv) > 2 else None
    raw = b""
    try:
        raw = sys.stdin.buffer.read()
        try:
            if rev is not None and not REV.fullmatch(rev):
                raise ValueError("bad rev %r" % rev)
            registry, hooks, _path = load_entry(event, key, rev)
        except ValueError as exc:
            note = ("hook-dispatch: registry unreadable (%s); the %s '%s' guards did not run. Restore the per-hook "
                    "config: python3 ~/.agents/policy/coding-agents/install-policy.py --hook-dispatcher off\n"
                    % (exc, event, key))
            return answer(refusal(event, note))
        if os.environ.get("HOOK_DISPATCH_TRACE"):
            sys.addaudithook(_audit)
        # inproc_sha pins each in-process guard to the bytes the parity fixture passed on; a changed guard runs as
        # its own process until install-policy reruns the fixture.
        inproc_names = registry.get("inproc_sha") if isinstance(registry.get("inproc_sha"), dict) else \
            dict.fromkeys(name for name in registry.get("inproc") or [] if isinstance(name, str))
        result = run_entry(event, hooks, raw, inproc_names, key)
    except BaseException as exc:
        # Anything this process did not plan for (no temp file, no process, a bug) refuses: the guards it was
        # running might have.
        note = ("hook-dispatch: failed (%s: %s); the %s '%s' guards did not all run%s\n"
                % (exc.__class__.__name__, str(exc)[:200], event, key,
                   ", so this call is refused" if event == "PreToolUse" else ""))
        try:
            return answer(refusal(event, note))
        except BaseException:
            return 2 if event == "PreToolUse" else 1
    return answer(result)


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # in-process guards may leave non-daemon threads or exit handlers; the answer is already out
