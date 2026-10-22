from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from scripts.restart_budget import EXCHANGE_SETTLE_S
from scripts.restart_context import RestartContext, SandboxRefused
from scripts.restart_sandbox import (
    check_sandbox_bootstrap,
    check_sandbox_interpreter,
    sandbox_program,
    sandbox_python_for,
)
from scripts.restart_state import (
    ensure_dir,
    now_iso,
    open_append,
    read_plist,
    read_state,
    remove_state,
    sandbox_dir_fd,
    say,
    set_preferred,
    sync_log,
    verify_sandbox_ancestry,
    write_state,
)


def http_get(context: RestartContext, port: int, path: str, timeout: float = 3.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return (resp.status, resp.read())
    except urllib.error.HTTPError as exc:
        return (exc.code, b"")
    except Exception:
        return (0, b"")


def healthy(context: RestartContext, port: int) -> bool:
    code, body = http_get(context, port, "/health/ready")
    if code != 200:
        return False
    try:
        if json.loads(body).get("checks", {}).get("database") != "ok":
            return False
    except ValueError:
        return False
    code, _ = http_get(context, port, "/v1/models", timeout=5.0)
    return code == 200


def in_flight(context: RestartContext, port: int, key: str = "in_flight") -> int | None:
    code, body = http_get(context, port, "/internal/drain/status", timeout=2.0)
    if code != 200:
        return None
    try:
        return int(json.loads(body)["checks"][key])
    except (ValueError, KeyError, TypeError):
        return None


def oauth_pending(context: RestartContext, port: int) -> tuple[int, list[float]] | None:
    code, body = http_get(context, port, "/internal/oauth/pending", timeout=2.0)
    if code != 200:
        return None
    try:
        data = json.loads(body)
        return (int(data["pending"]), [float(age) for age in data.get("ages_seconds", [])])
    except (ValueError, KeyError, TypeError):
        return None


def wait_for_oauth_flows(context: RestartContext, port: int, bound: float, exchanges: bool = False) -> float:
    start = time.monotonic()
    label = "token_exchanges" if exchanges else "oauth flows"
    previous = object()
    while True:
        waited = time.monotonic() - start
        status = None if exchanges else oauth_pending(context, port)
        count = in_flight(context, port, "token_exchanges") or 0 if exchanges else status[0] if status else None
        if count != previous:
            detail = f"; flow ages {status[1]}s" if status else ""
            message = f"lb-restart waiting on {count} {label} on :{port} (bound {bound:.0f}s){detail}"
            say(context, message)
            sync_log(context, message)
            previous = count
        if count == 0 or (count is None and (not exchanges)) or waited >= bound:
            sync_log(context, f"lb-restart {label} :{port}: {count} pending after {waited:.0f}s")
            return waited
        time.sleep(context.OAUTH_POLL_S)


def established(context: RestartContext, port: int) -> int:
    out = subprocess.run(
        [context.LSOF, "-nP", f"-iTCP:{port}", "-sTCP:ESTABLISHED"], capture_output=True, text=True
    ).stdout
    return sum((1 for line in out.splitlines()[1:] if f":{port}->" in line))


def launchctl_print(context: RestartContext, label: str) -> subprocess.CompletedProcess:
    if context.SANDBOX_ROOT is not None and label != context.LABEL:
        raise SandboxRefused(f"launchctl print of {label} refused in a sandbox restart (only {context.LABEL})")
    return subprocess.run(["launchctl", "print", f"gui/{context.UID}/{label}"], capture_output=True, text=True)


def launchd_pid(context: RestartContext, label: str) -> int | None:
    out = launchctl_print(context, label).stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("pid = "):
            return int(line.split("=", 1)[1])
    return None


def pid_alive(context: RestartContext, pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


def listener_pid(context: RestartContext, port: int) -> int | None:
    out = subprocess.run(
        [context.LSOF, "-nP", f"-iTCP@127.0.0.1:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True
    ).stdout.split()
    return int(out[0]) if out else None


def wait_until(context: RestartContext, pred, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def owned_by_sandbox(context: RestartContext, pid: int) -> bool:
    if context.SANDBOX_ROOT is None:
        return True
    cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    root = str(context.SANDBOX_ROOT)
    return any((token == root or token.startswith(root + os.sep) for token in cmd.split()))


def front_understands_preference(context: RestartContext) -> bool:
    pid = listener_pid(context, context.FRONT_PORT)
    got = read_state(context, context.FRONT_STATE)
    if got is None:
        return False
    try:
        state = json.loads(got[0])
    except ValueError:
        return False
    fresh = time.time() - got[1] < 5
    return bool(pid and state.get("pid") == pid and fresh and (context.STANDBY_PORT in state.get("upstreams", [])))


def front_routes_to(context: RestartContext, port: int, timeout: float = 5.0) -> bool:

    def ok() -> bool:
        got = read_state(context, context.FRONT_STATE)
        try:
            return got is not None and json.loads(got[0]).get("preferred") == port
        except (ValueError, AttributeError):
            return False

    return wait_until(context, ok, timeout)


def plist_env(context: RestartContext) -> dict[str, str]:
    data = read_plist(context)
    return {str(k): str(v) for k, v in data.get("EnvironmentVariables", {}).items()}


def plist_program(context: RestartContext) -> list[str]:
    return list(read_plist(context)["ProgramArguments"])


def start_standby(context: RestartContext, drain_seconds: int) -> subprocess.Popen:
    env = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG") if k in os.environ}
    env.update(plist_env(context))
    env["AGENT_LB_METRICS_ENABLED"] = "false"
    env["UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN"] = str(drain_seconds)
    args = [a for a in plist_program(context)]
    try:
        i = args.index("--port")
        args[i + 1] = str(context.STANDBY_PORT)
    except ValueError:
        args += ["--port", str(context.STANDBY_PORT)]
    ensure_dir(context, context.STANDBY_LOG.parent)
    log = open_append(context, context.STANDBY_LOG)
    log.write(f"\n==== {now_iso(context)} standby start ====\n")
    log.flush()
    if context.SANDBOX_ROOT is None:
        proc = subprocess.Popen(
            args,
            cwd=context.RUNTIME,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        verify_sandbox_ancestry(context)
        if args[: len(sandbox_program(context, context.SANDBOX_ROOT))] != sandbox_program(
            context, context.SANDBOX_ROOT
        ):
            raise SandboxRefused("standby program is not `<venv python> -I <installed lb-sandbox> _serve <root>`")
        check_sandbox_interpreter(context, Path(args[0]), context.SANDBOX_ROOT)
        check_sandbox_bootstrap(context, context.SANDBOX_ROOT)
        try:
            runtime_fd = sandbox_dir_fd(context, context.RUNTIME, create=False)
        except OSError as exc:
            raise SandboxRefused(f"{context.RUNTIME} is a link or missing inside the sandbox root") from exc
        try:
            proc = subprocess.Popen(
                args,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(runtime_fd,),
                preexec_fn=lambda: os.fchdir(runtime_fd),
            )
        finally:
            os.close(runtime_fd)
    ensure_dir(context, context.STATE_DIR)
    write_state(context, context.STANDBY_PIDFILE, f"{proc.pid}\n")
    return proc


def signal_group(context: RestartContext, pid: int, sig: int) -> bool:
    try:
        os.killpg(os.getpgid(pid), sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            return False
    return True


def stop_pid_gracefully(
    context: RestartContext, pid: int, port: int, bound: float, force: bool = False
) -> tuple[float, bool]:
    if not force:
        wait_for_oauth_flows(context, port, EXCHANGE_SETTLE_S, exchanges=True)
    start = time.monotonic()
    if not signal_group(context, pid, signal.SIGTERM):
        return (0.0, False)
    last = 0.0
    while pid_alive(context, pid) and time.monotonic() - start < bound:
        if time.monotonic() - last >= 10:
            last = time.monotonic()
            say(context, f"  :{port} draining, {established(context, port)} connection(s) open")
        time.sleep(0.25)
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
    forced = False
    if pid_alive(context, pid):
        forced = True
        signal_group(context, pid, signal.SIGKILL)
    return (time.monotonic() - start, forced)


def stop_leftover_standby(
    context: RestartContext, drain_seconds: int, hold_for_oauth=lambda port: None, force: bool = False
) -> None:
    pid = listener_pid(context, context.STANDBY_PORT)
    if pid is None:
        remove_state(context, context.STANDBY_PIDFILE)
        return
    say(context, f"found a standby still on :{context.STANDBY_PORT} (pid {pid}) from an earlier run")
    if not owned_by_sandbox(context, pid):
        raise SystemExit(f"pid {pid} on :{context.STANDBY_PORT} is not this sandbox's standby; refusing to signal it")
    if not healthy(context, context.PRIMARY_PORT):
        raise SystemExit("primary unhealthy and a standby is serving; fix by hand before restarting")
    hold_for_oauth(context.STANDBY_PORT)
    set_preferred(context, context.PRIMARY_PORT)
    front_routes_to(context, context.PRIMARY_PORT)
    if not force:
        wait_for_oauth_flows(context, context.STANDBY_PORT, EXCHANGE_SETTLE_S, exchanges=True)
    signal_group(context, pid, signal.SIGTERM)
    spawn_reaper(context, pid, drain_seconds)
    wait_until(context, lambda: listener_pid(context, context.STANDBY_PORT) is None, 15)
    say(context, "leftover standby stopped listening; it drains in the background")
    remove_state(context, context.STANDBY_PIDFILE)


def is_standby(context: RestartContext, pid: int) -> bool:
    out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return "agent-lb" in out and f"--port {context.STANDBY_PORT}" in out and owned_by_sandbox(context, pid)


def spawn_reaper(context: RestartContext, pid: int, drain_seconds: int) -> None:
    argv = [sys.executable, str(context.entry), "--reap", str(pid), str(drain_seconds + context.REAP_MARGIN_S)]
    env = None
    if context.SANDBOX_CONFIG is not None:
        argv = [argv[0], "-I", *argv[1:], "--sandbox", str(context.SANDBOX_CONFIG)]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "DYLD_"))}
    subprocess.Popen(
        argv,
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def reap(context: RestartContext, pid: int, deadline_s: float) -> int:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if not pid_alive(context, pid) or not is_standby(context, pid):
            return 0
        time.sleep(2)
    if not pid_alive(context, pid) or not is_standby(context, pid):
        return 0
    try:
        os.kill(pid, signal.SIGUSR2)
        time.sleep(2)
    except OSError:
        pass
    signal_group(context, pid, signal.SIGKILL)
    sync_log(
        context,
        f"lb-restart reaper: standby pid {pid} still alive {int(deadline_s)}s after SIGTERM; stack dump requested (SIGUSR2, forensics log), then SIGKILL",
    )
    return 1


def job_loaded(context: RestartContext, label: str) -> bool:
    return launchctl_print(context, label).returncode == 0


def parse_job_print(context: RestartContext, text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    scalars: dict[str, str] = {}
    blocks: dict[str, list[str]] = {}
    lines = text.splitlines()
    if not lines or not lines[0].endswith(" = {"):
        raise SandboxRefused("launchctl print of the sandbox job is not one job")
    current: str | None = None
    flags: set[str] = set()
    for line in lines[1:]:
        if current is not None:
            if line == "\t}":
                current = None
            elif line.startswith("\t\t"):
                blocks[current].append(line[2:])
            else:
                raise SandboxRefused("launchctl print of the sandbox job has a line outside its block")
            continue
        if line in ("", "}"):
            continue
        if not line.startswith("\t") or line.startswith("\t\t"):
            raise SandboxRefused("launchctl print of the sandbox job has a line at the wrong depth")
        if line[1:] in context.JOB_FLAG_LINES and line[1:] not in flags:
            flags.add(line[1:])
            continue
        key, sep, value = line[1:].partition(" = ")
        if not sep or key in scalars or key in blocks:
            raise SandboxRefused(f"launchctl print of the sandbox job repeats or mangles {key!r}")
        if value == "{":
            blocks[key], current = ([], key)
        else:
            scalars[key] = value
    if current is not None:
        raise SandboxRefused("launchctl print of the sandbox job ends inside a block")
    for name, entries in blocks.items():
        if name not in context.JOB_ENV_BLOCKS and name != "arguments" and any((" => " in entry for entry in entries)):
            raise SandboxRefused(f"launchctl print of the sandbox job has environment entries in {name!r}")
    return (scalars, blocks)


def job_env_block(context: RestartContext, entries: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for entry in entries:
        key, sep, value = entry.partition(" => ")
        if not sep or key in env:
            raise SandboxRefused("launchctl print of the sandbox job has a malformed environment")
        env[key] = value
    return env


def check_loaded_job_print(context: RestartContext, text: str, plist: dict, python: Path, want: list[str]) -> None:
    scalars, blocks = parse_job_print(context, text)
    if scalars.get("program") != str(python) or blocks.get("arguments") != want:
        raise SandboxRefused(
            f"loaded job {context.LABEL} does not run exactly `lb-sandbox _serve` on the checked interpreter"
        )
    if "environment" not in blocks:
        raise SandboxRefused(f"loaded job {context.LABEL} shows no environment")
    env = job_env_block(context, blocks["environment"])
    if env.get("XPC_SERVICE_NAME", context.LABEL) != context.LABEL:
        raise SandboxRefused(f"loaded job {context.LABEL} names another service")
    own = {k: v for k, v in env.items() if k not in context.LAUNCHD_ENV_KEYS}
    expected = {str(k): str(v) for k, v in plist.get("EnvironmentVariables", {}).items()}
    if own != expected:
        raise SandboxRefused(f"loaded job {context.LABEL} has an environment other than its checked plist's")
    for name in ("inherited environment", "default environment"):
        if any((k.startswith("DYLD_") for k in job_env_block(context, blocks.get(name, [])))):
            raise SandboxRefused(f"loaded job {context.LABEL} inherits a DYLD_ variable")
    workdir = plist.get("WorkingDirectory")
    if scalars.get("working directory") != (str(workdir) if workdir is not None else None):
        raise SandboxRefused(f"loaded job {context.LABEL} runs in another working directory than its checked plist's")
    for shown, key in context.JOB_STREAMS.items():
        if scalars.get(shown) != (str(plist[key]) if key in plist else None):
            raise SandboxRefused(
                f"loaded job {context.LABEL} sends its {shown.split()[0]} elsewhere than its checked plist"
            )


def check_loaded_sandbox_job(context: RestartContext) -> None:
    assert context.SANDBOX_ROOT is not None
    python = sandbox_python_for(context, context.SANDBOX_ROOT)
    check_sandbox_interpreter(context, python, context.SANDBOX_ROOT)
    check_sandbox_bootstrap(context, context.SANDBOX_ROOT)
    plist = read_plist(context)
    out = launchctl_print(context, context.LABEL).stdout
    want = [*sandbox_program(context, context.SANDBOX_ROOT), "--host", "127.0.0.1", "--port", str(context.PRIMARY_PORT)]
    check_loaded_job_print(context, out, plist, python, want)


def kickstart_primary(context: RestartContext, wait: bool) -> None:
    if context.SANDBOX_ROOT is not None:
        check_loaded_sandbox_job(context)
    argv = ["launchctl", "kickstart", "-k", f"gui/{context.UID}/{context.LABEL}"]
    if wait:
        subprocess.run(argv, check=False)
    else:
        subprocess.Popen(argv)


def restart_primary(context: RestartContext, reload_plist: bool, old_pid: int | None, bound: float) -> None:
    if not reload_plist:
        kickstart_primary(context, wait=False)
        return
    context.WATCHDOG_PAUSE.touch()
    try:
        subprocess.run(["launchctl", "bootout", f"gui/{context.UID}/{context.LABEL}"], timeout=bound, check=False)
        wait_until(
            context, lambda: not pid_alive(context, old_pid) and (not job_loaded(context, context.LABEL)), bound, 0.25
        )
        deadline = time.monotonic() + 30
        while True:
            rc = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{context.UID}", str(context.PLIST)], capture_output=True
            ).returncode
            if rc == 0 or job_loaded(context, context.LABEL):
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"launchctl bootstrap kept failing (rc={rc})")
            time.sleep(0.5)
    finally:
        context.WATCHDOG_PAUSE.unlink(missing_ok=True)


def graceful_seconds(context: RestartContext) -> int:
    env = plist_env(context)
    return int(env.get("UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN", "75"))


def exit_timeout(context: RestartContext) -> int:
    return int(read_plist(context).get("ExitTimeOut", 20))
