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
  guard answered nothing, this process execs into it, so the usual Bash call costs one process per event.
- external: everything else, exactly as configured.

Answer: one guard with output passes through byte for byte, except an exit 2 without a JSON block reason, which
is answered as exit 2 plus the JSON block Claude Code would have built (`[<guard command>]: <stderr>`), so the
message the model sees keeps the guard's own command. Several: any block wins and the block messages are joined in
config order; JSON answers merge (deny > ask > allow, contexts joined, the last updatedInput in config order wins);
plain stdout and errors follow Claude Code's per-event rules.

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


class Result(object):
    __slots__ = ("code", "out", "err", "timed_out", "mode", "spawned")

    def __init__(self, code=0, out=b"", err=b"", timed_out=False, mode="", spawned=0):
        self.code, self.out, self.err, self.timed_out = code, out, err, timed_out
        self.mode, self.spawned = mode, spawned

    def empty(self):
        """Nothing Claude Code acts on: exit 0 and no stdout (stderr of an exit-0 hook is ignored)."""
        return not self.timed_out and self.code == 0 and not self.out


class _GuardTimeout(BaseException):
    pass


# ---------------------------------------------------------------------------------------------------- environment


def registry_path():
    return os.environ.get("HOOK_DISPATCH_REGISTRY") or os.path.join(HERE, "dispatch", "registry.json")


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
    literals = ("Chrome.app/Contents/MacOS", "Chromium.app/Contents/MacOS", "--remote-debugging-port", "--headless",
                "puppeteer.launch", "chromium.launch", "launchPersistentContext")
    return (any(text in command for text in literals) or ("open" in command and "Chrom" in command)
            or ("pc" in command and "chrome" in command.lower()))  # Chrome on Alex's PC (factory f76e54a5a)


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


# Script guards: basename of the resolved script -> (sha256 of the reviewed bytes, prefilter).
SCRIPT_PREFILTERS = {
    "agent-lb-bootout-guard.sh": ("8f4a0eea5f7639e7d0f6e8d02a048ffd00db3c37daa9b6f4acb86d612a36d304", pf_bootout),
    "display-wake.sh": ("e701783e043516bdca4397ae0a1acdb8838645ee125931921e345704ce64056b", pf_display_wake),
    "wide-scan-guard.sh": ("fab6ad8cdc002a48698f0fe9bd19e6232492e67ce646f5873e54a7681ef270ac", pf_wide_scan),
    "link-cli-guard.sh": ("cb71baf54200c8dfcd06ba98bb269cb954ed82d11a32ad63a0394d576482ef22", pf_link_cli),
    "no-chrome-guard.sh": ("02f4782aadb16febacc1544cfc84dc270d28350fae29c2ce77facdd511b68b82", pf_no_chrome),
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
                 "fallback_text", "source", "reason", "prefilter")

    def __init__(self, hook):
        self.hook = hook
        self.command = hook.get("command", "") if isinstance(hook.get("command"), str) else ""
        timeout = hook.get("timeout")
        self.timeout = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else DEFAULT_TIMEOUT
        self.kind, self.script, self.argv, self.interp = "external", None, None, None
        self.devnull, self.fallback_true, self.fallback_text, self.source = False, False, None, None
        self.reason, self.prefilter = "", None


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
        plan.kind, plan.prefilter, plan.reason = "filtered", prefilter, "pinned command"
        return plan
    match = SHAPE.match(command)
    if not match:
        plan.reason = "shape"
        return plan
    if match.group("interp") is None:
        # A pinned shell guard, bare or inside `2>/dev/null || true`: when its prefilter says it cannot act, the
        # wrapped command ends with exit 0 and no output either way.
        pinned = SCRIPT_PREFILTERS.get(os.path.basename(os.path.realpath(expand_word(match.group("script")))))
        if pinned is not None:
            if sha256_file(os.path.realpath(expand_word(match.group("script")))) == pinned[0]:
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
    plan.devnull = bool(match.group("devnull"))
    plan.fallback_true = bool(match.group("true"))
    plan.fallback_text = match.group("fb")
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
        fd, self.path = tempfile.mkstemp(prefix="hook-dispatch-")
        try:
            while raw:
                raw = raw[os.write(fd, raw):]
        finally:
            os.close(fd)

    def open_fd(self):
        return os.open(self.path, os.O_RDONLY)

    def close(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass


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
    out_file, err_file = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    in_fd = payload.open_fd()
    os.dup2(in_fd, 0)
    os.close(in_fd)
    os.dup2(out_file.fileno(), 1)
    os.dup2(err_file.fileno(), 2)
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


class External(object):
    """A hook in its own process, through /bin/sh -c, as Claude Code runs it; drained by a reader thread."""

    def __init__(self, plan, payload):
        import subprocess
        import threading
        self.plan, self.started = plan, time.monotonic()
        self.outcome = None
        stdin = payload.open_fd()
        try:
            self.proc = subprocess.Popen([SHELL, "-c", plan.command], stdin=stdin, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True)
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
        except Exception as exc:  # the dispatcher could not read it; report as a non-blocking failure
            self.outcome = Result(1, b"", ("hook-dispatch: %s\n" % exc).encode(), False, "external", 1)

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


def deny_reason(obj):
    if not obj:
        return None
    specific = obj.get("hookSpecificOutput") if isinstance(obj.get("hookSpecificOutput"), dict) else {}
    if specific.get("permissionDecision") == "deny":
        return specific.get("permissionDecisionReason") or obj.get("reason") or "Blocked by hook"
    if obj.get("decision") == "block":
        return obj.get("reason") or "Blocked by hook"
    return None


def block_message(plan, result, obj):
    """The text Claude Code shows for a blocking hook: its JSON block reason, else `[<command>]: <stderr>`."""
    reason = deny_reason(obj)
    if reason:
        return reason
    return "[%s]: %s" % (plan.command, result.err.decode("utf-8", "replace") or "No stderr output")


def encode(obj):
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def merge(event, plans, results):
    """One answer for Claude Code from the results of every guard of the entry, in config order.

    Claude Code (2.1.293) reads a hook's stdout as JSON whatever its exit code; a JSON deny or `decision: block`
    blocks with its reason, and an exit 2 without one blocks with `[<command>]: <stderr>`. The dispatcher answers
    with that same text as a JSON block reason, so the message keeps the guard's own command, not the dispatcher's.
    """
    pairs = [(p, r) for p, r in zip(plans, results) if r is not None and not r.empty()]
    pairs = [(p, r) for p, r in pairs if not r.timed_out]  # a cancelled hook has no effect
    if not pairs:
        return Result(0, b"", b"")
    parsed = [(p, r, parse_json(r.out) if r.out else None) for p, r in pairs]
    blocks = [(p, r, obj) for p, r, obj in parsed if r.code == 2 or deny_reason(obj)]
    if len(pairs) == 1 and not (blocks and blocks[0][1].code == 2 and not deny_reason(blocks[0][2])):
        _p, only, _obj = parsed[0]
        return Result(only.code, only.out, only.err)  # byte for byte
    objects = [obj for _p, _r, obj in parsed if obj is not None]
    plain = [r.out for _p, r, obj in parsed if obj is None and r.code == 0 and r.out]
    errors = [r for _p, r, obj in parsed if obj is None and r.code not in (0, 2)]
    if blocks:
        messages = [block_message(p, r, obj) for p, r, obj in blocks]
        merged = merge_objects(event, objects, plain) if objects else {}
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
    if not objects and not plain:
        return Result(errors[0].code, b"", b"".join(r.err for r in errors))
    if not objects:
        return Result(0, b"".join(plain), b"".join(r.err for r in errors))
    return Result(0, encode(merge_objects(event, objects, plain)), b"".join(r.err for r in errors))


def merge_objects(event, objects, plain):
    merged, specific = {}, {}
    contexts, reasons_by_rank, system = [], {}, []
    decision, decision_reasons, stop_reasons = None, [], []
    for obj in objects:
        for key, value in obj.items():
            if key == "hookSpecificOutput" and isinstance(value, dict):
                for skey, svalue in value.items():
                    if skey == "additionalContext":
                        if svalue not in (None, ""):
                            contexts.append(str(svalue))
                    elif skey == "permissionDecision":
                        rank = _RANK.get(svalue, 0)
                        reasons_by_rank.setdefault(rank, [])
                        if value.get("permissionDecisionReason"):
                            reasons_by_rank[rank].append(value["permissionDecisionReason"])
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
                        decision_reasons.append(obj["reason"])
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


def load_registry():
    """The registry, else its backup copy. Raises when neither reads."""
    errors = []
    for path in (registry_path(), registry_path() + ".bak"):
        try:
            with open(path, encoding="utf-8") as handle:
                registry = json.load(handle)
            if isinstance(registry, dict) and isinstance(registry.get("entries"), dict):
                return registry
            errors.append("%s: not a registry" % path)
        except (OSError, ValueError) as exc:
            errors.append("%s: %s" % (path, exc.__class__.__name__))
    raise ValueError("; ".join(errors))


def dedupe(hooks):
    seen, kept = set(), []
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
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


def exec_into(plan, payload, record):
    """Replace this process with the rewriter: same pid, its own timeout kept by an inherited alarm."""
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
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(max(1, int(round(plan.timeout))))
    try:
        os.execv(target, words)
    except OSError:
        signal.alarm(0)


def updates_input(result):
    obj = parse_json(result.out) if result is not None and result.code == 0 and result.out else None
    specific = obj.get("hookSpecificOutput") if obj and isinstance(obj.get("hookSpecificOutput"), dict) else {}
    return "updatedInput" in specific


def run_entry(event, hooks, raw, inproc_names):
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
                    needed = plan.prefilter(payload_obj)
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
            results[index] = apply_wrapper(plan, result)
        for index, plan in enumerate(plans):
            if plan.kind != "rewriter":
                continue
            known = [(i, r) for i, r in enumerate(results) if r is not None]
            blocked = any(r.code == 2 or deny_reason(parse_json(r.out)) for _i, r in known if not r.timed_out)
            superseded = any(i > index and updates_input(r) for i, r in known)
            if blocked or superseded:
                results[index] = Result(0, b"", b"", False, "skipped (%s)" % ("blocked" if blocked else "superseded"))
                continue
            others_quiet = not started and all(r.empty() for _i, r in known) and \
                all(p.kind == "rewriter" or results[i] is not None for i, p in enumerate(plans) if i != index)
            if others_quiet and sum(1 for p in plans if p.kind == "rewriter") == 1:
                record["hooks"] = trace_hooks(plans, results)
                record["spawned"] = _SPAWNS[0]
                exec_into(plan, payload, record)
            started[index] = External(plan, payload)
        for index, external in started.items():
            results[index] = external.result()
        record["hooks"] = trace_hooks(plans, results)
        record["spawned"] = _SPAWNS[0]
        answer = merge(event, plans, results)
        record["answer"] = {"code": answer.code, "out": answer.out.decode("utf-8", "replace"),
                            "err": answer.err.decode("utf-8", "replace")}
        trace(record)
        return answer
    finally:
        payload.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] not in EVENTS:
        sys.stderr.write("usage: hook-dispatch.py <%s> <matcher>\n" % "|".join(EVENTS))
        return 0
    event, key = argv[0], argv[1]
    raw = sys.stdin.buffer.read()
    try:
        registry = load_registry()
        if key not in (registry.get("entries", {}).get(event) or {}):
            raise ValueError("no %s '%s' entry" % (event, key))  # settings and registry disagree: half installed
    except ValueError as exc:
        note = ("hook-dispatch: registry unreadable (%s); the %s '%s' guards did not run. Restore the per-hook "
                "config: python3 ~/.agents/policy/coding-agents/install-policy.py --hook-dispatcher off\n"
                % (exc, event, key))
        if event == "PreToolUse":  # the guards here can deny; refuse rather than skip them silently
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": note}}) + "\n")
            sys.stderr.write(note)
            return 2
        sys.stderr.write(note)
        return 1
    hooks = dedupe(registry.get("entries", {}).get(event, {}).get(key) or [])
    if not hooks:
        return 0
    if os.environ.get("HOOK_DISPATCH_TRACE"):
        sys.addaudithook(_audit)
    # inproc_sha pins each in-process guard to the bytes the parity fixture passed on; a changed guard runs as its
    # own process until install-policy reruns the fixture.
    inproc_names = registry.get("inproc_sha") if isinstance(registry.get("inproc_sha"), dict) else \
        dict.fromkeys(registry.get("inproc") or [])
    result = run_entry(event, hooks, raw, inproc_names)
    out, err = sys.stdout.buffer, sys.stderr.buffer
    if result.out:
        out.write(result.out)
        out.flush()
    if result.err:
        err.write(result.err)
        err.flush()
    return result.code


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # in-process guards may leave non-daemon threads or exit handlers; the answer is already out
