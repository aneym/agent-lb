from concurrent.futures import ThreadPoolExecutor
import os
import select
import shutil
import signal
import sys
import tempfile
import time
from parity_cases import HEAVY_DIR, bash, payload_for
from parity_execution import hook_timeout
from parity_registry import new_hooks, old_hooks


PROCESS_COMMANDS = (
    "ls -la",
    "git status",
    "echo hello",
    "cat README.md",
    "python3 -c 'print(1)'",
    "rg -n foo src",
    "find . -name '*.py'",
    "git log --oneline -5",
    "cd %s && pytest -q" % HEAVY_DIR,
    "jq . package.json",
    "npm run build",
    "sed -n 1,20p a.txt",
)



_NATIVE = {}



def _native():
    if not _NATIVE:
        import ctypes

        _NATIVE["ctypes"] = ctypes
        _NATIVE["proc"] = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        _NATIVE["libc"] = ctypes.CDLL(None, use_errno=True)
    return _NATIVE



def child_pids(pid):
    native = _native()
    ctypes = native["ctypes"]
    buf = (ctypes.c_int * 4096)()
    count = native["proc"].proc_listchildpids(pid, buf, ctypes.sizeof(buf))
    return [buf[i] for i in range(max(0, min(count, 4096))) if buf[i] > 0]



def reaped_children(pid):
    native = _native()
    ctypes = native["ctypes"]
    buf = (ctypes.c_uint64 * 20)()
    if native["proc"].proc_pid_rusage(pid, 1, buf) != 0:
        return None
    return buf[17] > 0



def proc_argv(pid):
    native = _native()
    ctypes, libc = native["ctypes"], native["libc"]
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0 or not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return None
    raw = buf.raw[: size.value]
    argc = int.from_bytes(raw[:4], sys.byteorder)
    parts = raw[4:].split(b"\0")
    index = 1
    while index < len(parts) and parts[index] == b"":
        index += 1
    return [part.decode("utf-8", "replace") for part in parts[index : index + argc]]



def runs_dispatcher(pid):
    argv = proc_argv(pid) or []
    return any(word.endswith("hook-dispatch.py") for word in argv[:3])



def observe(command, payload, env, cwd, timeout, grace=5.0):
    row = {
        "observed": False,
        "processes": None,
        "processes_lower": None,
        "hook_layer": None,
        "code": None,
        "why": None,
        "tree": [],
    }
    if not hasattr(select, "kqueue") or sys.platform != "darwin":
        row["why"] = "no kqueue/libproc on this platform"
        return row
    work = tempfile.mkdtemp(prefix="observe-", dir=env.get("TMPDIR") or None)
    paths = {name: os.path.join(work, name) for name in ("in", "out", "err")}
    with open(paths["in"], "wb") as handle:
        handle.write(payload)
    fds = [
        os.open(paths["in"], os.O_RDONLY),
        os.open(paths["out"], os.O_WRONLY | os.O_CREAT, 0o600),
        os.open(paths["err"], os.O_WRONLY | os.O_CREAT, 0o600),
    ]
    gate_r, gate_w = os.pipe()
    root = os.fork()
    if root == 0:
        try:
            os.close(gate_w)
            os.read(gate_r, 1)
            os.close(gate_r)
            os.setsid()
            os.chdir(cwd)
            for target, fd in enumerate(fds):
                os.dup2(fd, target)
            os.execve("/bin/sh", ["/bin/sh", "-c", command], env)
        finally:
            os._exit(127)
    os.close(gate_r)
    for fd in fds:
        os.close(fd)
    own_group = os.getpgrp()
    kq = select.kqueue()
    known = {root: {"parent": None, "by_dispatcher": False}}
    forks, live, stopped, missed, gaps = {}, set(), set(), [], []
    flags = select.KQ_NOTE_FORK | select.KQ_NOTE_EXEC | select.KQ_NOTE_EXIT

    def watch(pid):
        try:
            kq.control(
                [
                    select.kevent(
                        pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=flags
                    )
                ],
                0,
                0,
            )
            live.add(pid)
            return True
        except OSError:
            return False

    def freeze(pids):
        for pid in pids:
            try:
                group = os.getpgid(pid)
            except OSError:
                continue
            if group != own_group and group not in stopped:
                try:
                    os.killpg(group, signal.SIGSTOP)
                    stopped.add(group)
                except OSError:
                    pass

    def thaw():
        for group in list(stopped):
            try:
                os.killpg(group, signal.SIGCONT)
            except OSError:
                pass
        stopped.clear()

    def sweep():
        frontier = [(pid, False) for pid in known]
        while frontier:
            pid, new = frontier.pop()
            for child in child_pids(pid):
                if child in known:
                    continue
                known[child] = {"parent": pid, "by_dispatcher": pid in live and runs_dispatcher(pid)}
                freeze([child])
                watch(child)
                frontier.append((child, True))

            if new and reaped_children(pid) is not False:
                gaps.append(pid)

    watching = watch(root)
    if not watching:
        row["why"] = "kqueue watch failed"
    os.write(gate_w, b"x")
    os.close(gate_w)
    started = time.monotonic()
    root_exit = None
    status = None
    try:
        while watching:
            now = time.monotonic()
            if root_exit is None and now - started > timeout:
                row["why"] = "no exit within %ss" % timeout
                break
            if root_exit is not None and not live:
                break
            if root_exit is not None and now - root_exit > grace:
                row["why"] = "processes outlived the hook by %ss: %s" % (grace, sorted(live))
                break
            wait = max(0.0, (timeout - (now - started)) if root_exit is None else grace - (now - root_exit))
            forked = False
            for event in kq.control(None, 64, min(wait, 0.5)):
                if event.fflags & select.KQ_NOTE_FORK:
                    forks[event.ident] = forks.get(event.ident, 0) + 1
                    forked = True
                if event.fflags & select.KQ_NOTE_EXIT:
                    live.discard(event.ident)
                    if event.ident == root and root_exit is None:
                        root_exit = time.monotonic()
            if forked:
                try:
                    freeze(list(live))
                    sweep()
                finally:
                    thaw()
            if root_exit is not None and status is None:
                _pid, status = os.waitpid(root, 0)
    finally:
        thaw()
        kq.close()
        leftover = sorted(live)
        if leftover:
            for pid in leftover:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except OSError:
                    pass
        if status is None:
            try:
                os.killpg(root, signal.SIGKILL)
            except OSError:
                pass
            _pid, status = os.waitpid(root, 0)
        shutil.rmtree(work, ignore_errors=True)
    for pid, count in forks.items():
        found = sum(1 for info in known.values() if info["parent"] == pid)
        if count > found:
            missed.append({"pid": pid, "fork_events": count, "children_found": found})
    row["code"] = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
    row["tree"] = [
        {"pid": pid, "parent": info["parent"], "by_dispatcher": info["by_dispatcher"]}
        for pid, info in sorted(known.items())
    ]
    row["processes"] = len(known)

    row["processes_lower"] = max(len(known), 1 + sum(forks.values()))
    row["hook_layer"] = 1 + sum(1 for info in known.values() if info["by_dispatcher"])
    row["missed"] = missed
    row["gaps"] = gaps
    if missed and not row["why"]:
        row["why"] = "a process forked more children than were found: %s" % missed
    if gaps and not row["why"]:
        row["why"] = "a process reaped a child before it was watched: %s" % gaps
    row["observed"] = bool(
        watching and root_exit is not None and not leftover and not missed and not gaps and not row["why"]
    )
    return row



SELFTEST = (
    ("exec /usr/bin/true", 1, 1),
    ("/usr/bin/true; /usr/bin/true", 3, 1),
    ("sleep 0; exec /usr/bin/true", 2, 1),
    (
        'python3 -c \'import subprocess, sys; [subprocess.run([sys.executable, "-c", "pass"]) for _ in range(2)]\'; '
        "exit 0",
        4,
        1,
    ),
    (
        'exec python3 -c \'import subprocess, sys; subprocess.run([sys.executable, "-c", "import subprocess, sys; '
        '[subprocess.run([\\"/usr/bin/true\\"]) for _ in range(2)]"])\'',
        4,
        1,
    ),
)



def counter_selftest(env, cwd):
    rows = []
    for command, processes, hook_layer in SELFTEST:
        for attempt in range(1, 4):
            seen = observe(command, b"", env, cwd, 30)
            if seen["observed"]:
                break
        rows.append(
            {
                "command": command,
                "expected": processes,
                "processes": seen["processes"],
                "hook_layer": seen["hook_layer"],
                "observed": seen["observed"],
                "why": seen["why"],
                "attempts": attempt,
                "ok": seen["observed"] and seen["processes"] == processes and seen["hook_layer"] == hook_layer,
            }
        )
    return {"ok": all(row["ok"] for row in rows), "cases": rows}



CWD_CLAUDE = "under HOME (a .claude directory above)"



CWD_PLAIN = "outside HOME (no .claude directory above)"



def measure_call(sandbox, registry, command, where, path):
    total, layer, detail, complete = 0, 0, [], True
    for event in ("PreToolUse", "PostToolUse"):
        run = sandbox.fresh()
        cwd_rel = "home/project" if where == CWD_CLAUDE else "work"
        os.makedirs(os.path.join(run, cwd_rel), exist_ok=True)
        case = dict(bash(command, cwd_rel=cwd_rel), event=event, response={"stdout": ""}, name="count")
        payload = payload_for(case, run)
        env = sandbox.env(run)
        hooks = (
            new_hooks(registry["dispatch"], event, "Bash")
            if path == "dispatcher"
            else old_hooks(registry["per_hook"], event, "Bash")
        )
        for hook in hooks:
            seen = observe(hook["command"], payload, env, os.path.join(run, cwd_rel), hook_timeout(hook) + 5)
            complete = complete and seen["observed"]
            total += (seen["processes"] if path == "dispatcher" else seen["processes_lower"]) or 0
            layer += seen["hook_layer"] or 0
            detail.append(
                {
                    "event": event,
                    "command": hook["command"][:80],
                    "processes": seen["processes"],
                    "processes_lower": seen["processes_lower"],
                    "hook_layer": seen["hook_layer"],
                    "observed": seen["observed"],
                    "why": seen["why"],
                }
            )
        shutil.rmtree(run, ignore_errors=True)
    return {"processes": total, "hook_layer": layer, "complete": complete, "detail": detail}



def process_count(sandbox, registry, jobs=4):
    pairs = [(where, command) for where in (CWD_CLAUDE, CWD_PLAIN) for command in PROCESS_COMMANDS]

    def one(pair):
        where, command = pair

        for attempt in range(1, 4):
            new = measure_call(sandbox, registry, command, where, "dispatcher")
            if new["complete"]:
                break
        old = measure_call(sandbox, registry, command, where, "per-hook")
        ok = new["complete"] and new["hook_layer"] <= 2 and new["processes"] <= old["processes"]
        return {
            "command": command,
            "cwd": where,
            "attempts": attempt,
            "processes": new["processes"] if new["complete"] else None,
            "hook_layer": new["hook_layer"] if new["complete"] else None,
            "per_hook_processes": old["processes"],
            "per_hook_complete": old["complete"],
            "ok": ok,
            "detail": new["detail"],
            "per_hook_detail": old["detail"],
        }

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(one, pairs))

