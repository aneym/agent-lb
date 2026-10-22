import builtins
import io
import json
import os
import signal
import sys
import tempfile
import time
import types

from hook_dispatch_answers import Result  # type: ignore[missing-import]
from hook_dispatch_planning import HERE, RECEIPT_ENV, SHELL, guard_code, which  # type: ignore[missing-import]


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


def rewriter_env():
    env = dict(os.environ)
    git = which("git")
    if git:
        folder = os.path.dirname(git)
        path = env.get("PATH", os.defpath)
        if path.split(os.pathsep)[0] != folder:
            env["PATH"] = folder + os.pathsep + path
    return env


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


class _GuardTimeout(BaseException):
    pass


SPAWNS = [0]


def audit(event, _args):
    if event in (
        "subprocess.Popen",
        "os.posix_spawn",
        "os.fork",
        "os.forkpty",
        "os.system",
        "os.spawn",
        "os.startfile",
    ):
        SPAWNS[0] += 1


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
    spawned_before = SPAWNS[0]

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
    result = Result(code, out_file.read(), err_file.read(), timed_out, "inproc", SPAWNS[0] - spawned_before)
    out_file.close()
    err_file.close()
    if crashed:
        result.mode = "inproc-crash"
    return result, crashed
