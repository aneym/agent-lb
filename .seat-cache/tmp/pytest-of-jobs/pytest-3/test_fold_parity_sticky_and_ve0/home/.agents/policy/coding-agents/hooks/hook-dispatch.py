#!/usr/bin/env python3

import builtins
import hashlib
import io
import json
import marshal
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import types

sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.realpath(__file__))]
try:
    from hook_failure_recovery import answer_problem, deny_reason, parse_json  # type: ignore[missing-import]
    from hook_output import cap_context, merge_objects  # type: ignore[missing-import]
    from hook_prefilters import COMMAND_PREFILTERS, SCRIPT_NEEDS, SCRIPT_PREFILTERS, escapes_in_input, never  # type: ignore[missing-import]
except ImportError as missing:
    pre = sys.argv[1:2] == ["PreToolUse"]
    why = (
        "hook-dispatch: a helper beside it is missing (%s); its guards did not run%s. Restore the per-hook config: "
        "python3 ~/.agents/policy/coding-agents/install-policy.py --hook-dispatcher off\n"
        % (missing, ", so this call is refused" if pre else "")
    )
    sys.stderr.write(why)
    if pre:
        verdict = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why.strip()}
        sys.stdout.write(json.dumps({"hookSpecificOutput": verdict}))
    sys.exit(2 if pre else 1)

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
DEFAULT_TIMEOUT = 5.0
FLOOR_TIMEOUT = 40.0
DISPATCH_TIMEOUT = 50.0
SHELL = "/bin/sh"
HERE = os.path.dirname(os.path.abspath(__file__))
REWRITERS = {"rtk hook claude"}
REV = re.compile(r"[0-9a-f]{12}")
REV_SHA = re.compile(r"[0-9a-f]{64}")

FLOOR_SCRIPTS = (
    "branch-guard.py",
    "rm-dynamic-deny",
    "stash-guard",
    "railway-vars-guard.sh",
    "link-cli-guard.sh",
    "plutil-guard.sh",
    "kill-guard",
    "agent-lb-bootout-guard.sh",
    "dangerous-command-guard.sh",
    "no-chrome-guard.sh",
    "host-credential-guard.py",
    "gh-write-guard.py",
    "kill-scope-guard.py",
)
FLOOR_WORD = re.compile(r"(?:^|[/\s\"'])(%s)(?=$|[\s\"';|&)])" % "|".join(re.escape(name) for name in FLOOR_SCRIPTS))
FLOOR_TEXT = "BLOCKED: Dangerous command"
INLINE_FLOOR = "dangerous-command"

LEGACY_FLOOR_COMMANDS = {
    "0fbf6582d067534e0cf7179a1801b12c675c75397170a48d36aec72171483e7c": '"$HOME/.claude/hooks/dangerous-command-guard.sh"',
    "85663684c44150f611a408af4d442c891ce1b66ba43f3e47610b7262c9b26525": '/usr/bin/python3 "$HOME/.claude/hooks/seat-guard.py"',
    "487abcd9095bc74d791c3371413cf424ba2a562e10e1f4a4e2f559d63f71de89": '/usr/bin/python3 "$HOME/.claude/hooks/workflow-seat-guard.py"',
}
RECEIPT_ENV = "HOOK_FLOOR_RECEIPT"
RECEIPT = "floor-ok %s"
NOTICE_PREFIX = "Failed with non-blocking status code: "

ENV_SENSITIVE = (
    "BASH_ENV",
    "ENV",
    "BASHOPTS",
    "SHELLOPTS",
    "POSIXLY_CORRECT",
    "GREP_OPTIONS",
    "GLOBIGNORE",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
)

HAZARD = re.compile(
    r"os\._exit|os\.fork|os\.exec|os\.setsid|os\.dup2|os\.closerange|os\.kill\(\s*os\.getpid|threading|_thread|"
    r"multiprocessing|concurrent\.futures|asyncio|atexit|\bsignal\.|faulthandler|ctypes|sys\.settrace|sys\.setprofile|"
    r"except\s*:|except\s+BaseException|except\s*\(\s*BaseException|sys\.modules\[\s*['\"]__main__|importlib\.reload|"
    r"sys\.__std(?:in|out|err)__|os\.close\(\s*[012]\s*\)"
)

DUP2_STD = re.compile(r"os\.dup2\((?:[^()]|\([^()]*(?:\([^()]*\))?[^()]*\))*,\s*[012]\s*\)")
IMPORT_NAMES = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))", re.M
)

_PATH_WORD = (
    r'"\$HOME/[^"$`\\]*"|\$HOME/[^\s"\'$`\\;&|<>(){}*?\[\]]*|~/[^\s"\'$`\\;&|<>(){}*?\[\]]*|'
    r'"/[^"$`\\]*"|/[^\s"\'$`\\;&|<>(){}*?\[\]]+'
)
_ARG_WORD = r"[A-Za-z0-9_./=:+-]+"
SHAPE = re.compile(
    r"^\s*(?:(?P<interp>/usr/bin/python3|python3)\s+)?(?P<script>%s)(?P<args>(?:\s+%s)*)"
    r"(?P<devnull>\s+2>\s*/dev/null)?"
    r"(?:\s+\|\|\s+(?:(?P<true>true)|\{\s*printf\s+%%s\s+'(?P<fb>[^']*)'\s*;\s*\}))?\s*$" % (_PATH_WORD, _ARG_WORD)
)

IF_READABLE = re.compile(
    r'^if \[ -r "(?P<path>\$HOME/[^"$`\\]+)" \]; then (?P<run>(?:/usr/bin/python3|python3) "(?P=path)"); '
    r"else printf '%s\\n' \"hook warning: missing (?P=path); continuing\" >&2; fi(?: #[^\n]*)?$"
)

FLOOR_EXEC = re.compile(
    r"^\s*(?:(?P<interp>/usr/bin/python3|python3|/bin/bash|bash)\s+)?(?P<script>%s)(?P<args>(?:\s+%s)*)\s*$"
    % (_PATH_WORD, _ARG_WORD)
)


class Result(object):
    __slots__ = ("code", "out", "err", "timed_out", "mode", "spawned", "floor_failure", "receipt")

    def __init__(self, code=0, out=b"", err=b"", timed_out=False, mode="", spawned=0):
        self.code, self.out, self.err, self.timed_out = code, out, err, timed_out
        self.mode, self.spawned = mode, spawned
        self.floor_failure = None
        self.receipt = False

    def empty(self):
        return not self.timed_out and self.code == 0 and not self.out


class _GuardTimeout(BaseException):
    pass


def registry_path():
    return os.environ.get("HOOK_DISPATCH_REGISTRY") or os.path.join(HERE, "dispatch", "registry.json")


def env_sensitive():
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
        return home + word[len("$HOME") :]
    if word.startswith("~/"):
        return home + word[1:]
    return word


def interpreter_of(script):
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


class Plan(object):
    __slots__ = (
        "hook",
        "command",
        "timeout",
        "kind",
        "script",
        "argv",
        "interp",
        "devnull",
        "fallback_true",
        "fallback_text",
        "source",
        "reason",
        "prefilter",
        "floor",
        "exec_argv",
        "opaque",
    )

    def __init__(self, hook):
        self.hook = hook
        self.command = hook.get("command", "") if isinstance(hook.get("command"), str) else ""
        timeout = hook.get("timeout")
        self.timeout = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else DEFAULT_TIMEOUT
        self.kind, self.script, self.argv, self.interp = "external", None, None, None
        self.devnull, self.fallback_true, self.fallback_text, self.source = False, False, None, None
        self.reason, self.prefilter = "", None
        self.floor = floor_name(self.command)
        self.timeout = (
            min(float(timeout), FLOOR_TIMEOUT)
            if self.floor and isinstance(timeout, (int, float)) and timeout > 0
            else (FLOOR_TIMEOUT if self.floor else min(self.timeout, DEFAULT_TIMEOUT))
        )
        self.exec_argv = None
        self.opaque = None


def legacy_floor(command):
    return LEGACY_FLOOR_COMMANDS.get(hashlib.sha256(command.encode("utf-8")).hexdigest()) if command else None


def floor_name(command):
    if not command:
        return None
    match = FLOOR_WORD.search(legacy_floor(command) or command)
    if match:
        return match.group(1)
    return INLINE_FLOOR if FLOOR_TEXT in command else None


def floor_exec(command, name):
    match = FLOOR_EXEC.match(legacy_floor(command) or command)
    if not match:
        return None
    script = expand_word(match.group("script"))
    if os.path.basename(script) != name:
        return None
    return ([match.group("interp")] if match.group("interp") else []) + [script] + match.group("args").split()


def sibling_sources(script_dir, source, depth=0, seen=None):
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


def cached(key, build, kind):
    path = os.path.join(HERE, "__pycache__", "hook-dispatch-" + key)
    try:
        with open(path, "rb") as handle:
            value = marshal.load(handle)
        if isinstance(value, kind):
            return value
    except (OSError, EOFError, ValueError, TypeError):
        pass
    value = build()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as handle:
            marshal.dump(value, handle)
        os.replace(temp, path)
    except (OSError, ValueError):
        pass
    return value


def guard_code(plan):
    key = hashlib.sha256((plan.script + "\0" + plan.source).encode("utf-8")).hexdigest()
    tag = sys.implementation.cache_tag or "python"
    return cached(
        "code-%s-%s" % (tag, key), lambda: compile(plan.source, plan.script, "exec", dont_inherit=True), types.CodeType
    )


def plan_hook(hook, inproc_names, script_pins=None):
    plan = Plan(hook)
    command = plan.command
    if hook.get("type", "command") != "command" or not command:
        plan.reason = "not a command hook"
        return plan
    if plan.floor:
        plan.exec_argv = floor_exec(command, plan.floor)
        if plan.exec_argv is None:
            plan.reason = "floor guard"
            plan.opaque = (
                "is not registered as a direct exec of its own script (`[python3|bash] <script> [args]`; "
                "a floor guard never runs through a shell, S44)"
            )
            return plan
        command = legacy_floor(command) or command
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
    guarded = IF_READABLE.match(command)
    if guarded:
        if not os.access(expand_word(guarded.group("path")), os.R_OK):
            plan.kind, plan.prefilter, plan.reason = "filtered", never, "its script is missing, so it only warns"
            return plan
        command = guarded.group("run")
    match = SHAPE.match(command)
    if not match:
        plan.reason = "shape"
        return plan
    plan.devnull = bool(match.group("devnull"))
    plan.fallback_true = bool(match.group("true"))
    plan.fallback_text = match.group("fb")
    script = expand_word(match.group("script"))
    name = os.path.basename(os.path.realpath(script))
    if match.group("interp") is None:
        pinned = SCRIPT_PREFILTERS.get(name)
        if pinned is not None:
            sensitive = env_sensitive()
            hashes = pinned[0] if isinstance(pinned[0], tuple) else (pinned[0],)
            derived = (script_pins or {}).get(name) if isinstance(script_pins, dict) else None
            if isinstance(derived, str) and REV_SHA.fullmatch(derived):
                hashes = hashes + (derived,)
            missing = [need[0] for need in SCRIPT_NEEDS.get(name, ()) if not any(which(tool) for tool in need)]
            if match.group("fb") is not None:
                plan.reason = "pinned script with a fallback answer"
            elif sensitive:
                plan.reason = "pinned script, but %s is set" % sensitive
            elif missing:
                plan.reason = "pinned script, but %s is not on PATH" % ", ".join(missing)
            elif sha256_file(os.path.realpath(script)) in hashes:
                plan.kind, plan.prefilter, plan.reason = "filtered", pinned[1], "pinned script"
            else:
                plan.reason = "script changed since its prefilter was reviewed"
            return plan
    if name not in inproc_names:
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
    pinned = inproc_names.get(name) if isinstance(inproc_names, dict) else None
    if pinned and hashlib.sha256(data).hexdigest() != pinned:
        plan.reason = "changed since the parity fixture passed on it"
        return plan
    texts = [source] + sibling_sources(os.path.dirname(os.path.realpath(script)), source)
    key = hashlib.sha256("\0".join(texts).encode("utf-8")).hexdigest()
    if cached("hazard-" + key, lambda: any(hazardous(text) for text in texts), bool):
        plan.reason = "hazard"
        return plan
    plan.script, plan.interp, plan.source = script, interp, source
    plan.argv = [script] + match.group("args").split()
    plan.kind = "inproc"
    plan.reason = "same interpreter" if same_interpreter(interp) else "other interpreter"
    return plan


_SPAWNS = [0]


def _audit(event, _args):
    if event in (
        "subprocess.Popen",
        "os.posix_spawn",
        "os.fork",
        "os.forkpty",
        "os.system",
        "os.spawn",
        "os.startfile",
    ):
        _SPAWNS[0] += 1


class Payload(object):
    def __init__(self, raw):
        self.raw = raw
        self.path = None
        self.restore()

    def restore(self):
        fd, path = tempfile.mkstemp(prefix="hook-dispatch-")
        try:
            raw = self.raw
            while raw:
                raw = raw[os.write(fd, raw) :]
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
    sys.stdout = io.TextIOWrapper(io.BufferedWriter(io.FileIO(1, "w", closefd=False)), encoding=enc_out, errors=err_out)
    sys.stderr = io.TextIOWrapper(
        io.BufferedWriter(io.FileIO(2, "w", closefd=False)),
        encoding=enc_err,
        errors="backslashreplace",
        line_buffering=True,
    )
    module = types.ModuleType("__main__")
    module.__file__ = plan.script
    module.__builtins__ = builtins
    module.__cached__ = None
    module.__spec__ = None
    module.__package__ = None
    sys.modules["__main__"] = module
    sys.argv = list(plan.argv)
    sys.path[0:1] = [os.path.dirname(os.path.realpath(plan.script))]
    if plan.floor:
        os.environ[RECEIPT_ENV] = "1"
    code, timed_out, crashed = 0, False, False
    spawned_before = _SPAWNS[0]

    def on_alarm(_signum, _frame):
        raise _GuardTimeout()

    previous = signal.signal(signal.SIGALRM, on_alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, plan.timeout)
        try:
            exec(guard_code(plan), module.__dict__)
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
    if result.timed_out:
        return result
    if plan.devnull:
        result.err = b""
    if result.code != 0:
        if plan.fallback_true:
            result.code = 0
        elif plan.fallback_text is not None:
            result.out += plan.fallback_text.encode("utf-8")
            result.code = 0
    return result


def take_receipt(result, name):
    want = (RECEIPT % name).encode("utf-8")
    body = result.out[:-1] if result.out.endswith(b"\n") else result.out
    cut = body.rfind(b"\n") + 1
    if body[cut:] == want:
        result.out, result.receipt = body[:cut], True
    return result


def floor_failure(result):
    if result.timed_out:
        return "timed out"
    if result.code == 2:
        return None
    if result.code == 127:
        return "is missing (exit 127)"
    if result.code != 0:
        return "crashed (exit %d)" % result.code
    if not result.receipt:
        return "exited 0 without its allow receipt"
    return answer_problem(result.out)


def finish(plan, raw):
    if plan.floor:
        take_receipt(raw, plan.floor)
        raw.floor_failure = floor_failure(raw)
        return raw
    if plan.kind == "inproc":
        return apply_wrapper(plan, raw)
    return raw


class External(object):
    def __init__(self, plan, payload):
        import subprocess
        import threading

        self.plan, self.started = plan, time.monotonic()
        self.outcome = None
        self.thread = None
        argv, executable, env = [SHELL, "-c", plan.command], None, None
        if plan.exec_argv:
            argv, env = list(plan.exec_argv), dict(os.environ, **{RECEIPT_ENV: "1"})
        elif plan.kind == "rewriter":
            words = plan.command.split()
            target = which(words[0])
            if target:
                argv, executable = words, target
            env = rewriter_env()
        stdin = payload.open_fd()
        try:
            self.proc = subprocess.Popen(
                argv,
                executable=executable,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            if not plan.exec_argv:
                raise

            code = 127 if isinstance(exc, FileNotFoundError) else 126
            self.outcome = Result(
                code,
                b"",
                ("hook-dispatch: %s: %s\n" % (argv[0], exc.strerror or exc)).encode("utf-8", "backslashreplace"),
                False,
                "external",
                0,
            )
            return
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
        except Exception as exc:
            self.outcome = Result(
                2,
                b"",
                ("hook-dispatch: could not read this guard's answer (%s); refused\n" % exc.__class__.__name__).encode(),
                False,
                "external",
                1,
            )

    def result(self, deadline):
        if self.thread is not None:
            self.thread.join(max(0, deadline - time.monotonic()))
        if self.outcome is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
            return Result(1, b"", b"", True, "external", 1)
        return self.outcome


def block_message(plan, result, obj):
    reason = deny_reason(obj)
    if reason:
        return reason
    return "[%s]: %s" % (plan.command, result.err.decode("utf-8", "replace") or "No stderr output")


def encode(obj):
    return (json.dumps(obj, ensure_ascii=True) + "\n").encode("ascii")


def refusal(event, note, blocked=False):
    err = (note.rstrip("\n") + "\n").encode("utf-8", "backslashreplace")
    if event == "PreToolUse":
        obj = {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": note,
            }
        }
    elif blocked:
        obj = {"decision": "block", "reason": note}
    else:
        return Result(1, b"", err, mode="refused")
    return Result(2, encode(obj), err, mode="refused")


def notice_text(result):
    return NOTICE_PREFIX + (result.err.decode("utf-8", "replace").strip() or "No stderr output")


def refuse_failed_floor(plan, result):
    if result is None or not result.floor_failure:
        return result
    note = "hook-dispatch: floor guard %s %s, so this call is refused (a floor guard that fails denies)" % (
        plan.floor,
        result.floor_failure,
    )
    detail = result.err.decode("utf-8", "replace").strip()[-500:]
    return Result(
        2,
        b"",
        (note + ("\n" + detail if detail else "") + "\n").encode("utf-8", "backslashreplace"),
        False,
        "floor-refused",
        result.spawned,
    )


def merge(event, plans, results):
    try:
        answer = merge_answers(event, plans, results)
        answer.out = cap_context(answer.out, event)
        return answer
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
            messages.append("hook-dispatch: could not merge the guards' answers (%s); refused" % exc.__class__.__name__)
        return refusal(event, "\n".join(messages), blocked)


def merge_answers(event, plans, results):
    if event == "PreToolUse":
        results = [refuse_failed_floor(p, r) for p, r in zip(plans, results)]
    pairs = [(p, r) for p, r in zip(plans, results) if r is not None and not r.empty()]
    pairs = [(p, r) for p, r in pairs if not r.timed_out]
    if not pairs:
        return Result(0, b"", b"")
    parsed = [(p, r, parse_json(r.out) if r.out else None) for p, r in pairs]
    blocks = [(p, r, obj) for p, r, obj in parsed if r.code == 2 or deny_reason(obj)]
    if len(pairs) == 1 and not (blocks and blocks[0][1].code == 2 and not deny_reason(blocks[0][2])):
        _p, only, _obj = parsed[0]
        return Result(only.code, only.out, only.err)

    items = [
        obj if obj is not None else notice_text(r) for _p, r, obj in parsed if obj is not None or r.code not in (0, 2)
    ]
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
            specific.pop("updatedInput", None)
            specific["permissionDecision"] = "deny"
            specific["permissionDecisionReason"] = "\n".join(messages)
            merged["hookSpecificOutput"] = specific
        else:
            merged["decision"] = "block"
            merged["reason"] = "\n".join(messages)
        return Result(2, encode(merged), b"".join(r.err for _p, r, _obj in blocks))

    notice_err = (
        ("\n".join(notices)[len(NOTICE_PREFIX) :] + "\n").encode("utf-8", "backslashreplace") if notices else b""
    )
    if not objects and not plain:
        return Result(errors[0].code, b"", notice_err)
    if not objects and (not errors or event != "UserPromptSubmit"):
        return Result(errors[0].code if errors else 0, b"".join(plain), notice_err)

    return Result(0, encode(merge_objects(event, items, plain)), b"")


def registry_candidates(rev=None):
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
            errors.append(
                "%s: fold %s, not this entry's %s"
                % (os.path.basename(path), registry.get("rev") or "without a rev", rev or "(without a rev)")
            )
            continue
        if rev and entries_rev(registry["entries"]) != rev:
            errors.append("%s: entries do not hash to rev %s" % (os.path.basename(path), rev))
            continue
        try:
            return registry, entry_hooks(registry, event, key), path
        except ValueError as exc:
            errors.append("%s: %s" % (os.path.basename(path), exc))
    raise ValueError("; ".join(errors))


def entries_rev(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def entry_hooks(registry, event, key):
    hooks = (registry["entries"].get(event) or {}) if isinstance(registry["entries"].get(event), dict) else None
    if hooks is None or key not in hooks:
        raise ValueError("no %s '%s' entry" % (event, key))
    hooks = hooks[key]
    if not isinstance(hooks, list) or not hooks:
        raise ValueError("the %s '%s' entry is %s, not a list of hooks" % (event, key, type(hooks).__name__))
    for hook in hooks:
        if not (
            isinstance(hook, dict)
            and hook.get("type", "command") == "command"
            and isinstance(hook.get("command"), str)
            and hook["command"].strip()
        ):
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
            row.update(
                mode=result.mode,
                code=result.code,
                timed_out=result.timed_out,
                spawned=result.spawned,
                out=result.out.decode("utf-8", "replace"),
                err=result.err.decode("utf-8", "replace"),
            )
        rows.append(row)
    return rows


def rewriter_env():
    env = dict(os.environ)
    git = which("git")
    if git:
        folder = os.path.dirname(git)
        path = env.get("PATH", os.defpath)
        if path.split(os.pathsep)[0] != folder:
            env["PATH"] = folder + os.pathsep + path
    return env


def exec_into(plan, payload, record):
    words = plan.command.split()
    target = which(words[0])
    if not target:
        return
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
    return (
        event == "PreToolUse"
        and isinstance(payload, dict)
        and "agent_id" in payload
        and payload.get("tool_name") == "Bash"
    )


def suppress_subagent_owner(result, payload, event):
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
    rows = []
    for plan, result in zip(plans, results):
        if result is None or result.mode.startswith("skipped"):
            continue
        failure = (
            result.floor_failure
            if plan.floor
            else (
                "timed out" if result.timed_out else ("exited %d" % result.code if result.code not in (0, 2) else None)
            )
        )
        if failure:
            rows.append(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "event": event,
                    "key": key,
                    "command": plan.command[:300],
                    "floor": plan.floor,
                    "failure": failure,
                    "outcome": "refused" if plan.floor and event == "PreToolUse" else "failed open",
                }
            )
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


def run_entry(event, hooks, raw, inproc_names, key=None, script_pins=None):
    deadline = time.monotonic() + DISPATCH_TIMEOUT
    record = {"event": event, "pid": os.getpid(), "ppid": os.getppid()}
    plans = []
    for hook in hooks:
        try:
            plans.append(plan_hook(hook, inproc_names, script_pins))
        except Exception as exc:
            plan = Plan(hook)
            plan.reason = "plan error %s" % exc.__class__.__name__
            plans.append(plan)
    try:
        payload_obj = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        payload_obj = None
    payload = Payload(raw)
    results: list[Result | None] = [None] * len(plans)
    started = {}
    try:
        for index, plan in enumerate(plans):
            if plan.opaque and event == "PreToolUse":
                plan.kind, plan.reason = "opaque", plan.reason + ", " + plan.opaque
                results[index] = Result(
                    1,
                    b"",
                    ("hook-dispatch: %s\n" % plan.command[:300]).encode("utf-8", "backslashreplace"),
                    False,
                    "opaque",
                )
                results[index].floor_failure = plan.opaque
                continue
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
            plan.timeout = min(plan.timeout, max(0.001, deadline - time.monotonic()))
            try:
                result, crashed = run_inproc(plan, payload)
            except Exception as exc:
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
            plan.timeout = min(plan.timeout, max(0.001, deadline - time.monotonic()))
            known = [(i, r) for i, r in enumerate(results) if r is not None]
            blocked = any(r.code == 2 or deny_reason(parse_json(r.out)) for _i, r in known if not r.timed_out)
            superseded = any(i > index and updates_input(r) for i, r in known)
            if blocked or superseded:
                results[index] = Result(0, b"", b"", False, "skipped (%s)" % ("blocked" if blocked else "superseded"))
                continue

            others_quiet = (
                not started
                and all(r.empty() and not r.floor_failure for _i, r in known)
                and all(p.kind == "rewriter" or results[i] is not None for i, p in enumerate(plans) if i != index)
            )
            if (
                others_quiet
                and sum(1 for p in plans if p.kind == "rewriter") == 1
                and not subagent_bash(payload_obj, event)
            ):
                record["hooks"] = trace_hooks(plans, results)
                record["spawned"] = _SPAWNS[0]
                exec_into(plan, payload, record)
            started[index] = External(plan, payload)
        for index, external in started.items():
            results[index] = finish(plans[index], external.result(deadline))
        log_failures(event, key, plans, results)
        record["hooks"] = trace_hooks(plans, results)
        record["spawned"] = _SPAWNS[0]
        answer = suppress_subagent_owner(merge(event, plans, results), payload_obj, event)
        record["answer"] = {
            "code": answer.code,
            "out": answer.out.decode("utf-8", "replace"),
            "err": answer.err.decode("utf-8", "replace"),
        }
        trace(record)
        return answer
    finally:
        payload.close()


def answer(result):
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
        sys.stderr.write(
            "hook-dispatch: usage: hook-dispatch.py <%s> <matcher> [<rev>]; its guards did not run\n" % "|".join(EVENTS)
        )
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
            note = (
                "hook-dispatch: registry unreadable (%s); the %s '%s' guards did not run. Restore the per-hook "
                "config: python3 ~/.agents/policy/coding-agents/install-policy.py --hook-dispatcher off\n"
                % (exc, event, key)
            )
            return answer(refusal(event, note))
        if os.environ.get("HOOK_DISPATCH_TRACE"):
            sys.addaudithook(_audit)

        inproc_names = (
            registry.get("inproc_sha")
            if isinstance(registry.get("inproc_sha"), dict)
            else dict.fromkeys(name for name in registry.get("inproc") or [] if isinstance(name, str))
        )
        script_pins = registry.get("script_sha") if isinstance(registry.get("script_sha"), dict) else None
        result = run_entry(event, hooks, raw, inproc_names, key, script_pins)
    except BaseException as exc:
        note = "hook-dispatch: failed (%s: %s); the %s '%s' guards did not all run%s\n" % (
            exc.__class__.__name__,
            str(exc)[:200],
            event,
            key,
            ", so this call is refused" if event == "PreToolUse" else "",
        )
        try:
            return answer(refusal(event, note))
        except BaseException:
            return 2 if event == "PreToolUse" else 1
    return answer(result)


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
