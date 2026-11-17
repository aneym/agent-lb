#!/usr/bin/env python3
import json
import os
import re
import runpy
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.realpath(__file__))
REGISTRY = os.path.join(HERE, "advisory-fold")
EVENTS = ("PreToolUse", "PostToolUse")
SHA = re.compile(r"[0-9a-f]{12}")
SIMPLE = re.compile(r"[A-Za-z0-9_|]+")
DEFAULT_TIMEOUT = 60
INPROC = re.compile(
    r'(?P<interp>/usr/bin/python3|python3) "\$HOME/\.claude/hooks/(?P<name>[A-Za-z0-9_.-]+\.py)"(?P<args>(?: [A-Za-z0-9_-]+)*)'
)
HAZARD = (
    r"os\._exit|os\.fork|\bsignal\b|\batexit\b|multiprocessing|os\.exec|asyncio|concurrent\.futures"
    r"|os\.read\(\s*0|\.fileno\(\)|faulthandler|settrace|setprofile|os\.dup2?\(|os\.setsid|os\.kill|sys\.modules"
)
HAZARDS = {False: re.compile(HAZARD + r"|\bthreading\b"), True: re.compile(HAZARD)}
CAPTURED_THREADS = {"agent-home-hooks.py"}
IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


class Expired(BaseException):
    pass


def matches(matcher, tool):
    if matcher in (None, "", "*"):
        return True
    if SIMPLE.fullmatch(matcher):
        return tool in matcher.split("|")
    return re.search(matcher, tool) is not None


def missing_script(command):
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    interpreter = os.path.basename(words[0])
    if not (interpreter.startswith("python") or interpreter in {"bash", "sh", "node", "ruby", "perl"}):
        return None
    options = {"-I", "-S", "-E", "-s", "-u", "-B"} if interpreter.startswith("python") else set()
    for word in words[1:]:
        if word.startswith("-"):
            if word not in options:
                return None
            continue
        path = os.path.expanduser(os.path.expandvars(word))
        if any(mark in path for mark in ";&|<>()$`*?["):
            return None
        return path if not os.path.exists(path) else None
    return None


def hazardous(path, seen, threads):
    if path in seen:
        return False
    seen.add(path)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return True
    if HAZARDS[threads].search(text):
        return True
    for name in IMPORT.findall(text):
        sibling = os.path.join(os.path.dirname(path), name + ".py")
        if os.path.isfile(sibling) and hazardous(sibling, seen, False):
            return True
    return False


def same_python(interp):
    found = interp if interp.startswith("/") else shutil.which(interp)
    return bool(found) and os.path.realpath(found) == os.path.realpath(sys.executable)


def in_process(command):
    match = INPROC.fullmatch(command)
    if not match or not same_python(match.group("interp")):
        return None
    script = os.path.join(os.path.expanduser("~"), ".claude", "hooks", match.group("name"))
    threads = match.group("name") in CAPTURED_THREADS
    if not os.path.isfile(script) or hazardous(os.path.realpath(script), set(), threads):
        return None
    return script, match.group("args").split()


def exit_code(value):
    if value is None:
        return 0
    if isinstance(value, int):
        return value & 0xFF
    sys.stderr.write(str(value) + "\n")
    return 1


def expire(_signum, _frame):
    raise Expired()


def stream(fd, like, errors):
    return open(fd, "w" if fd else "r", encoding=like.encoding, errors=errors, closefd=False)


def run_inproc(script, args, raw, timeout):
    argv, path, env, cwd = sys.argv[:], sys.path[:], dict(os.environ), os.getcwd()
    modules, streams = set(sys.modules), (sys.stdin, sys.stdout, sys.stderr)
    saved = [os.dup(fd) for fd in (0, 1, 2)]
    files = [tempfile.TemporaryFile() for _ in range(3)]
    files[0].write(raw)
    files[0].flush()
    files[0].seek(0)
    for fd, handle in enumerate(files):
        os.dup2(handle.fileno(), fd)
    sys.stdin = stream(0, streams[0], streams[0].errors)
    sys.stdout = stream(1, streams[1], streams[1].errors)
    sys.stderr = stream(2, streams[2], "backslashreplace")
    code, expired = 0, False
    folder = os.path.dirname(os.path.realpath(script))
    try:
        sys.argv[:] = [script, *args]
        sys.path[0:1] = [folder]
        signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, timeout)
        runpy.run_path(script, run_name="__main__")
    except SystemExit as exc:
        code = exit_code(exc.code)
    except Expired:
        expired = True
    except BaseException:
        traceback.print_exc()
        code = 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for handle in (sys.stdout, sys.stderr):
            try:
                handle.flush()
            except (OSError, ValueError):
                pass
        for fd, copy in enumerate(saved):
            os.dup2(copy, fd)
            os.close(copy)
        sys.stdin, sys.stdout, sys.stderr = streams
        sys.argv[:], sys.path[:] = argv, path
        if dict(os.environ) != env:
            os.environ.clear()
            os.environ.update(env)
        if os.getcwd() != cwd:
            os.chdir(cwd)
        for name in set(sys.modules) - modules:
            origin = getattr(sys.modules[name], "__file__", None) or ""
            if os.path.dirname(os.path.realpath(origin)) == folder:
                del sys.modules[name]
    out, err = (read(handle) for handle in files[1:])
    files[0].close()
    if expired:
        return None, out, err + b"hook timed out\n"
    return code, out, err


def read(handle):
    handle.seek(0)
    data = handle.read()
    handle.close()
    return data


class External(object):
    def __init__(self, command, raw):
        self.files = [tempfile.TemporaryFile() for _ in range(3)]
        self.files[0].write(raw)
        self.files[0].flush()
        self.files[0].seek(0)
        self.proc = subprocess.Popen(
            ["/bin/sh", "-c", command],
            stdin=self.files[0],
            stdout=self.files[1],
            stderr=self.files[2],
            start_new_session=True,
        )

    def result(self, deadline):
        try:
            code = self.proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
            self.proc.wait()
            code = None
        self.files[0].close()
        out, err = (read(handle) for handle in self.files[1:])
        if code is None:
            return None, out, err + b"hook timed out\n"
        return (code if code >= 0 else 1), out, err


def parsed(out):
    head = out.decode("utf-8", "replace").lstrip().lstrip("﻿").lstrip()
    if head[:1] != "{":
        return None
    try:
        data = json.loads(head)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def refuses(data):
    specific = data.get("hookSpecificOutput")
    decision = specific.get("permissionDecision") if isinstance(specific, dict) else None
    return decision == "deny" or data.get("decision") == "block" or data.get("continue") is False


def asks(data):
    specific = data.get("hookSpecificOutput")
    return isinstance(specific, dict) and specific.get("permissionDecision") == "ask"


def merged(event, base, others):
    out = json.loads(json.dumps(base))
    contexts, messages = [], []
    for data in [base] + others:
        specific = data.get("hookSpecificOutput")
        if isinstance(specific, dict) and isinstance(specific.get("additionalContext"), str):
            contexts.append(specific["additionalContext"])
        if isinstance(data.get("systemMessage"), str):
            messages.append(data["systemMessage"])
    if contexts:
        specific = out.get("hookSpecificOutput") if isinstance(out.get("hookSpecificOutput"), dict) else {}
        specific.setdefault("hookEventName", event)
        specific["additionalContext"] = "\n".join(contexts)
        out["hookSpecificOutput"] = specific
    if messages:
        out["systemMessage"] = "\n".join(messages)
    return out


def answer(event, results):
    datas = [parsed(out) if code == 0 else None for code, out, _err in results]
    for (code, out, err), data in zip(results, datas):
        if code == 2:
            sys.stderr.buffer.write(err)
            return 2
        if data is not None and refuses(data):
            others = [other for other in datas if other is not None and other is not data]
            sys.stdout.write(json.dumps(merged(event, data, others)))
            return 0
    found = [data for data in datas if data is not None]
    base = next((data for data in found if asks(data)), None)
    if base is None and found:
        base = {}
    failed = [(code, err) for code, _out, err in results if code != 0]
    for code, err in failed:
        sys.stderr.buffer.write(err)
    if base is not None:
        sys.stdout.write(json.dumps(merged(event, base, [data for data in found if data is not base])))
        return 0
    plain = b"".join(out for code, out, _err in results if code == 0)
    if plain.strip():
        sys.stdout.buffer.write(plain)
        return 0
    if failed:
        return failed[0][0] if failed[0][0] is not None else 1
    return 0


def load(event, rev):
    with open(os.path.join(REGISTRY, rev + ".json"), encoding="utf-8") as handle:
        hooks = json.load(handle)[event]
    if not isinstance(hooks, list):
        raise ValueError("entry is not a list")
    return hooks


def main(argv):
    if len(argv) != 2 or argv[0] not in EVENTS or not SHA.fullmatch(argv[1]):
        sys.stderr.write("advisory-fold: usage: advisory-fold.py <PreToolUse|PostToolUse> <rev>\n")
        return 1
    event, rev = argv
    raw = sys.stdin.buffer.read()
    try:
        hooks = load(event, rev)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.stderr.write("advisory-fold: registry %s unreadable (%s); its advisory hooks did not run\n" % (rev, exc))
        return 1
    try:
        tool = json.loads(raw.decode("utf-8")).get("tool_name") or ""
    except (ValueError, UnicodeDecodeError, AttributeError):
        tool = ""
    chosen = [
        hook
        for hook in hooks
        if isinstance(tool, str) and matches(hook.get("matcher"), tool) and not missing_script(hook["command"])
    ]
    plans = [(hook, in_process(hook["command"])) for hook in chosen]
    started = {}
    for index, (hook, inproc) in enumerate(plans):
        if inproc is None:
            started[index] = (External(hook["command"], raw), time.monotonic() + hook.get("timeout", DEFAULT_TIMEOUT))
    results = {}
    for index, (hook, inproc) in enumerate(plans):
        if inproc is not None:
            results[index] = run_inproc(inproc[0], inproc[1], raw, hook.get("timeout", DEFAULT_TIMEOUT))
    for index, (external, deadline) in started.items():
        results[index] = external.result(deadline)
    return answer(event, [results[index] for index in range(len(plans))])


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(code)
