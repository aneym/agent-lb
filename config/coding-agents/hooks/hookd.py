#!/usr/bin/env python3

import importlib.util
import io
import os
import signal
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCHER = os.path.join(HERE, "hook-dispatch.py")
SOCKET = os.environ.get("HOOKD_SOCKET") or os.path.join(HERE, "dispatch", "hookd.sock")
WORKERS = int(os.environ.get("HOOKD_WORKERS") or 16)
RECYCLE = int(os.environ.get("HOOKD_RECYCLE") or 2000)
WATCHED = ("hook-dispatch.py", "hook_output.py", "hook_failure_recovery.py", "hookd.py")
MAGIC = b"HOOKD1"


def stamp():
    out: list[tuple[int, int]] = []
    for name in WATCHED:
        try:
            st = os.stat(os.path.join(HERE, name))
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((0, 0))
    return out


def load_dispatcher():
    spec = importlib.util.spec_from_file_location("hook_dispatch_live", DISPATCHER)
    if spec is None or spec.loader is None:
        raise SystemExit("hookd: cannot load %s" % DISPATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__dict__["DAEMON_MODE"] = True
    return module


def recv_exact(conn, size):
    chunks, left = [], size
    while left:
        chunk = conn.recv(min(left, 1 << 20))
        if not chunk:
            raise EOFError("client closed")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def recv_field(conn, buf):
    while b"\0" not in buf[0]:
        chunk = conn.recv(65536)
        if not chunk:
            raise EOFError("client closed")
        buf[0] += chunk
    field, buf[0] = buf[0].split(b"\0", 1)
    return field


def read_request(conn):
    buf = [b""]
    if recv_field(conn, buf) != MAGIC:
        raise ValueError("bad magic")
    argv = [recv_field(conn, buf).decode("utf-8", "surrogateescape") for _ in range(int(recv_field(conn, buf)))]
    env = {}
    for _ in range(int(recv_field(conn, buf))):
        key, _sep, value = recv_field(conn, buf).decode("utf-8", "surrogateescape").partition("=")
        env[key] = value
    cwd = recv_field(conn, buf).decode("utf-8", "surrogateescape")
    size = int(recv_field(conn, buf))
    raw, buf[0] = buf[0][:size], buf[0][size:]
    return argv, env, cwd, raw + recv_exact(conn, size - len(raw))


def serve_one(dispatcher, argv, env, cwd, raw):
    base_env, base_std = dict(os.environ), (sys.stdin, sys.stdout, sys.stderr)
    out, err = io.BytesIO(), io.BytesIO()
    os.environ.clear()
    os.environ.update(env)
    try:
        os.chdir(cwd)
    except OSError:
        pass
    sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    sys.stdout = io.TextIOWrapper(out, encoding="utf-8", write_through=True)
    sys.stderr = io.TextIOWrapper(err, encoding="utf-8", errors="backslashreplace", write_through=True)
    try:
        code = dispatcher.main(argv)
    except BaseException as exc:
        sys.stderr.write(
            "hookd: dispatcher failed (%s); the %s guards did not all run\n" % (exc.__class__.__name__, argv[:2])
        )
        code = 2 if argv[:1] == ["PreToolUse"] else 1
    finally:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        captured = out.getvalue(), err.getvalue()
        sys.stdin, sys.stdout, sys.stderr = base_std
        os.environ.clear()
        os.environ.update(base_env)
        os.chdir("/")
    return int(code or 0), *captured


def reply(conn, code, out, err):
    conn.sendall(b"%d\0%d\0" % (code, len(out)) + out + b"%d\0" % len(err) + err)


def worker(listener):
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    os.chdir("/")
    born = stamp()
    dispatcher = load_dispatcher()
    for _ in range(RECYCLE):
        conn, _addr = listener.accept()
        with conn:
            try:
                request = read_request(conn)
            except (OSError, ValueError, EOFError) as exc:
                sys.stderr.write("hookd: bad request (%s)\n" % exc)
                continue
            try:
                reply(conn, *serve_one(dispatcher, *request))
            except OSError as exc:
                sys.stderr.write("hookd: reply failed (%s)\n" % exc)
        if stamp() != born:
            break
    os._exit(0)


def spawn(listener):
    pid = os.fork()
    if pid == 0:
        try:
            worker(listener)
        finally:
            os._exit(1)
    return pid


def main():
    os.makedirs(os.path.dirname(SOCKET), exist_ok=True)
    try:
        os.unlink(SOCKET)
    except FileNotFoundError:
        pass
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o077)
    listener.bind(SOCKET)
    os.umask(old)
    listener.listen(512)
    pids = {spawn(listener): time.monotonic() for _ in range(WORKERS)}

    def stop(_signum, _frame):
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        try:
            os.unlink(SOCKET)
        except OSError:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while os.path.exists(SOCKET):
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            pid = 0
        if not pid:
            time.sleep(1)
            continue
        born = pids.pop(pid, 0.0)
        if time.monotonic() - born < 2:
            time.sleep(2)
        pids[spawn(listener)] = time.monotonic()
    stop(0, None)


if __name__ == "__main__":
    main()
