from __future__ import annotations

import hashlib
import math
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from claude_lb_common import (  # pyrefly: ignore[missing-import]
    CCGPT_MODE,
    CCGPT_MODEL,
    DEFAULT_CLAUDE_EFFORT,
    DEFAULT_CLAUDE_MODEL,
    EXIT_TEMPFAIL,
    STALL_STARTUP_GRACE,
    STALL_TIMEOUT_DEFAULT,
    WORK_PROCESS_POLL_SECONDS,
    LaunchConfig,
    activity_path,
    arg_value,
    client_activity_path,
    env_float,
    has_permission_option,
    headless_invocation,
)
from claude_lb_quota import (  # pyrefly: ignore[missing-import]
    RESUME_MAX_ATTEMPTS,
    claim_session_route,
    should_wait_for_reset,
    wait_max_seconds,
    wait_until_reset,
)


def without_option(argv: list[str], option: str) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for argument in argv:
        if skip_next:
            skip_next = False
            continue
        if argument == option:
            skip_next = True
            continue
        if argument.startswith(option + "="):
            continue
        cleaned.append(argument)
    return cleaned


def claude_executable() -> str:
    own = Path(__file__).resolve().with_name("claude-lb-launch")
    shadowed = False
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        candidate = Path(entry or ".") / "claude"
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            continue
        if candidate.resolve() == own:
            shadowed = True
            continue
        return str(candidate) if shadowed else "claude"
    if shadowed:
        print(
            f"cc: every claude on PATH is this launcher ({own}); refusing to start itself. "
            "Put the real Claude Code binary on PATH.",
            file=sys.stderr,
        )
        raise SystemExit(127)
    return "claude"


def build_command(argv: list[str]) -> list[str]:
    command = [claude_executable()]
    if CCGPT_MODE:
        argv = without_option(without_option(argv, "--model"), "-m")
        command.extend(["--model", CCGPT_MODEL])
    else:
        if arg_value(argv, "--model", "-m") is None and not os.environ.get("ANTHROPIC_MODEL"):
            command.extend(["--model", DEFAULT_CLAUDE_MODEL])
    effort_arg = arg_value(argv, "--effort")
    effort_env = (os.environ.get("CC_EFFORT_LEVEL") or "").strip()

    if effort_arg is None:
        if effort_env.lower() not in {"", "none", "off"}:
            command.extend(["--effort", effort_env])
        elif effort_env.lower() in {"none", "off"}:
            pass
        else:
            command.extend(["--effort", "high" if CCGPT_MODE else DEFAULT_CLAUDE_EFFORT])
    elif effort_arg.lower() in {"none", "off"}:
        argv = without_option(argv, "--effort")
    permission_mode = None if CCGPT_MODE else os.environ.get("CC_PERMISSION_MODE")
    if CCGPT_MODE and not has_permission_option(argv):
        permission_mode = "bypassPermissions"
    if permission_mode:
        command.extend(["--permission-mode", permission_mode])
    command.extend(argv)
    return command


def derive_session_id(argv: list[str]) -> str:

    explicit = os.environ.get("CLAUDE_LB_SESSION_ID")
    if explicit:
        return explicit
    if headless_invocation(argv):
        digest = hashlib.sha256(os.getcwd().encode()).hexdigest()[:12]
        return f"cc-headless-{digest}"
    return f"cc-{os.getpid()}-{int(time.time())}"


def auto_resume_enabled() -> bool:
    value = (os.environ.get("CLAUDE_LB_AUTO_RESUME") or "1").strip().lower()
    return value not in {"0", "never", "off", "false"}


def build_resume_command(argv: list[str], claude_session_id: str) -> list[str]:
    command = [claude_executable()]
    effort = (os.environ.get("CC_EFFORT_LEVEL") or "").strip()
    if effort and effort.lower() not in {"none", "off"}:
        command.extend(["--effort", effort])
    permission_mode = os.environ.get("CC_PERMISSION_MODE")
    if permission_mode:
        command.extend(["--permission-mode", permission_mode])
    for flag in ("--model", "--output-format"):
        value = arg_value(argv, flag)
        if value:
            command.extend([flag, value])
    command.extend(
        [
            "--resume",
            claude_session_id,
            "-p",
            "You were interrupted by a usage limit that has now reset. Continue the task exactly where you left off.",
        ]
    )
    return command


def stop_child(child: subprocess.Popen[bytes], *, interrupt: bool = False) -> None:
    if child.poll() is None:
        if not interrupt:
            child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def transcript_mtime(session_dir: Path, session_id: str) -> float | None:

    latest = None
    paths = [session_dir / f"{session_id}.jsonl"]
    subtree = session_dir / session_id

    remaining = 10000
    for root, dirs, files in os.walk(subtree, onerror=lambda error: None):
        paths.extend(Path(root) / name for name in files[:remaining])
        remaining -= min(len(files), remaining)
        if remaining <= 0:
            dirs.clear()
            break
        dirs[:] = dirs[:remaining]
        remaining -= len(dirs)
    for path in paths:
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if latest is None or modified > latest:
            latest = modified
    return latest


def parse_elapsed(text: str) -> int:

    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", text)
    if match is None:
        raise ValueError("invalid ps etime")
    days, hours, minutes, seconds = match.groups()
    return int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)


def shell_command_process(args: str) -> bool:

    words = args.split()
    if not words:
        return False
    shell = os.path.basename(words[0]).lstrip("-")
    if shell not in {"sh", "bash", "zsh", "dash", "ksh", "fish"}:
        return False
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--" or not word.startswith(("-", "+")):
            return False
        if word in {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}:
            index += 2
            continue
        if word.startswith("-") and not word.startswith("--") and word[1:].isalpha() and "c" in word[1:]:
            return True
        index += 1
    return False


def work_process_alive(child_pid: int) -> bool:

    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,etime=,args="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if output.returncode != 0:
            return True
        processes = {}
        for line in output.stdout.splitlines():
            pid, ppid, age, *args = line.split(maxsplit=3)
            processes[int(pid)] = (int(ppid), parse_elapsed(age), args[0] if args else "")
        if child_pid not in processes:
            return True
        parent_age = processes[child_pid][1]
        children = {}
        for pid, (ppid, age, args) in processes.items():
            children.setdefault(ppid, []).append((pid, age, args))
        descendants = [child_pid]
        while descendants:
            parent = descendants.pop()
            for pid, age, args in children.get(parent, ()):
                if shell_command_process(args) or age < parent_age - STALL_STARTUP_GRACE:
                    return True
                descendants.append(pid)
        return False
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return True


def activity_life(path: Path, now: float, wall_now: float, stall_timeout: float) -> float | None:
    try:
        inflight_text, progress_text = path.read_text().split()
        inflight = int(inflight_text)
        progress = float(progress_text)
        if inflight < 0 or not math.isfinite(progress):
            raise ValueError("invalid activity")
    except (OSError, ValueError):
        return None
    if inflight == 0:
        return now
    if wall_now - progress < stall_timeout:
        return now - max(0, wall_now - progress)
    return None


def run_supervised(
    command: list[str],
    activity: Path,
    stall_timeout: float,
    session_id: str | None = None,
) -> int:
    if not math.isfinite(stall_timeout) or stall_timeout <= 0:
        try:
            return subprocess.run(command).returncode
        finally:
            try:
                activity.unlink()
            except OSError:
                pass
    client_activity = client_activity_path(session_id) if session_id is not None else None
    if client_activity is not None:
        try:
            client_activity.touch()
        except OSError:
            pass
    start = time.monotonic()
    last_life = start
    session_dir = None
    next_lookup = start
    work_seen = False
    work_interval = min(WORK_PROCESS_POLL_SECONDS, stall_timeout / 4)
    next_work_check = start
    if session_id is not None:
        projects = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
        session_dir = projects / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    try:
        child = subprocess.Popen(command)
        while True:
            try:
                return child.wait(timeout=min(1.0, stall_timeout / 4))
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            wall_now = time.time()
            for path in (activity, client_activity):
                if path is not None:
                    life = activity_life(path, now, wall_now, stall_timeout)
                    if life is not None:
                        last_life = max(last_life, life)
            if session_dir is not None and session_id is not None:
                primary = session_dir / f"{session_id}.jsonl"
                if not primary.exists() and now >= next_lookup:
                    next_lookup = now + 30
                    try:
                        discovered = next(projects.glob(f"*/{session_id}.jsonl"), None)  # pyrefly: ignore[unbound-name]
                    except OSError:
                        discovered = None
                    if discovered is not None:
                        session_dir = discovered.parent
                modified = transcript_mtime(session_dir, session_id)
                if modified is not None and wall_now - modified < stall_timeout:
                    last_life = max(last_life, now - max(0, wall_now - modified))
            if now - last_life >= stall_timeout or (
                now >= next_work_check and (work_seen or now - last_life >= work_interval)
            ):
                alive = work_process_alive(child.pid)
                if alive or work_seen:
                    last_life = now
                work_seen = alive
                next_work_check = now + work_interval
            if now - last_life >= stall_timeout:
                print(
                    f"cc: no agent-lb traffic, transcript writes or tool processes for {stall_timeout:g}s "
                    f"(CLAUDE_LB_STALL_TIMEOUT); stopping claude, exit {EXIT_TEMPFAIL} (retryable)",
                    file=sys.stderr,
                )
                stop_child(child)
                return EXIT_TEMPFAIL
    except KeyboardInterrupt:
        stop_child(child, interrupt=True)  # pyrefly: ignore[unbound-name]
        return 130
    finally:
        for path in (activity, client_activity):
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass


def claude_session_args(argv: list[str], command: list[str]) -> tuple[list[str], str | None]:
    explicit_id = arg_value(argv, "--session-id")
    if explicit_id is not None:
        return command, explicit_id
    if "--fork-session" not in argv:
        resume_id = arg_value(argv, "--resume", "-r")
        if resume_id is not None:
            return command, resume_id
        if "--continue" in argv or "-c" in argv:
            return command, None
    session_id = str(uuid.uuid4())
    return [*command, "--session-id", session_id], session_id


def run_headless_with_resume(
    command: list[str],
    argv: list[str],
    session_id: str,
    model: str,
    quota_key: str,
) -> int:

    command, claude_session_id = claude_session_args(argv, command)
    deadline = time.time() + wait_max_seconds()
    current = command
    activity = activity_path(session_id, os.getpid())
    stall_timeout = env_float("CLAUDE_LB_STALL_TIMEOUT", STALL_TIMEOUT_DEFAULT)
    for attempt in range(RESUME_MAX_ATTEMPTS + 1):
        returncode = run_supervised(current, activity, stall_timeout, session_id=claude_session_id)
        if returncode in (0, EXIT_TEMPFAIL) or claude_session_id is None:
            return returncode

        claim, _, retry_at = claim_session_route(session_id, model, quota_key, LaunchConfig.base_url)
        if claim is not None or not should_wait_for_reset(retry_at, deadline):
            return returncode
        if attempt == RESUME_MAX_ATTEMPTS:
            return returncode
        try:
            wait_until_reset(retry_at, "Claude run interrupted by account limits.")
        except KeyboardInterrupt:
            print("cc: aborted while waiting for limits to reset", file=sys.stderr)
            return 130
        print(f"cc: resuming session {claude_session_id} (attempt {attempt + 1})", file=sys.stderr)
        current = build_resume_command(argv, claude_session_id)
    return 1
