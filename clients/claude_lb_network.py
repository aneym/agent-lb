from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from claude_lb_common import (  # pyrefly: ignore[missing-import]
    CCGPT_MODE,
    LOCAL_LB_URL,
    LaunchConfig,
    default_remote_url,
    env_float,
    remote_only_mac,
)


def lb_enabled() -> bool:
    return os.environ.get("CLAUDE_LB_DISABLE") != "1"


def doctor_path(name: str) -> Path:
    return Path(__file__).resolve().with_name(name)


def run_opus_doctor(nightly: bool = False) -> int:
    doctor = doctor_path("opus-runtime-doctor")
    if not doctor.is_file():
        print(f"cc: Opus runtime doctor is missing at {doctor}", file=sys.stderr)
        return 127
    command = [sys.executable, str(doctor)]
    if nightly:
        command.append("--nightly")
    command.append("--repair")
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as error:
        print(f"cc: Opus runtime doctor could not start ({type(error).__name__})", file=sys.stderr)
        return 126


def schedule_opus_doctor() -> None:

    if (
        os.environ.get("CLAUDE_LB_DOCTOR_SKIP") == "1"
        or os.environ.get("CLAUDE_LB_DISABLE") == "1"
        or os.environ.get("CLAUDE_LB_DRY_RUN") == "1"
        or CCGPT_MODE
    ):
        return
    env = {**os.environ, "CLAUDE_LB_DOCTOR_SKIP": "1"}
    definitions = doctor_path("agent-defs-doctor")
    if definitions.is_file():
        try:
            completed = subprocess.run(
                [sys.executable, str(definitions), "--repair", "--quiet"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=30,
                text=True,
                check=False,
            )
            summary = completed.stdout.strip()
            if summary:
                print(summary, file=sys.stderr)
            if completed.returncode != 0 and not summary:
                print(f"agent-defs-doctor: repair failed with exit {completed.returncode}", file=sys.stderr)
        except (OSError, subprocess.TimeoutExpired):
            print("agent-defs-doctor: repair could not complete", file=sys.stderr)
    runtime = doctor_path("opus-runtime-doctor")
    if runtime.is_file():
        try:
            subprocess.run(
                [sys.executable, str(runtime), "--schedule"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def bypass_anthropic_proxy() -> None:

    for name in ("NO_PROXY", "no_proxy"):
        exclusions = [entry.strip() for entry in os.environ.get(name, "").split(",") if entry.strip()]
        if not any(entry.lower() == "api.anthropic.com" for entry in exclusions):
            exclusions.append("api.anthropic.com")
        os.environ[name] = ",".join(exclusions)


def lb_json(path: str, *, timeout: float = 2.5, base_url: str | None = None) -> dict:
    request = urllib.request.Request(
        f"{base_url or LaunchConfig.base_url}{path}", headers={"accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def lb_post_json(path: str, payload: dict[str, str], *, timeout: float = 4.0, base_url: str | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url or LaunchConfig.base_url}{path}",
        data=body,
        headers={"accept": "application/json", "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")
    return json.loads(response_body) if response_body else {}


def is_unreachable_health_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return False
    reason = getattr(error, "reason", error)
    if isinstance(reason, ConnectionError):
        return True
    return isinstance(reason, OSError) and reason.errno in {
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETUNREACH,
    }


def is_timeout_error(error: BaseException) -> bool:

    if isinstance(error, urllib.error.HTTPError):
        return False
    reason = getattr(error, "reason", error)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return isinstance(reason, OSError) and reason.errno in {errno.ETIMEDOUT, errno.EAGAIN}


def probe_interactive_ready(base_url: str, *, timeout: float, retries: int, gap: float) -> tuple[str, str]:

    last_error = "no response"
    for attempt in range(max(1, retries)):
        try:
            lb_json("/health/ready", timeout=timeout, base_url=base_url)
            return "ready", ""
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error) or error.__class__.__name__
            if is_timeout_error(error):
                return "busy", last_error
            if is_unreachable_health_error(error):
                return "unreachable", last_error
            if attempt + 1 < retries:
                time.sleep(gap)
    return "down", last_error


def probe_health_at(base_url: str, *, retries: int, timeout: float, gap: float) -> tuple[bool, str, bool]:

    last_error = "no response"
    last_error_unreachable = False
    for attempt in range(retries):
        try:
            lb_json("/health/ready", timeout=timeout, base_url=base_url)
            return True, "", False
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error) or error.__class__.__name__
            last_error_unreachable = is_unreachable_health_error(error)
            if attempt + 1 < retries:
                time.sleep(gap)
    return False, last_error, last_error_unreachable


def local_launchd_job_loaded() -> bool:
    label = os.environ.get("CLAUDE_LB_LAUNCHD_LABEL") or "com.aneyman.agent-lb"
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def wait_for_local_startup(base_url: str, *, probe_timeout: float) -> bool:
    grace_seconds = max(0.0, env_float("CLAUDE_LB_STARTUP_GRACE_SECONDS", 90.0))
    if grace_seconds == 0 or not local_launchd_job_loaded():
        return False

    print(
        f"cc: agent-lb is starting — waiting up to {grace_seconds:g}s for /health/ready ...",
        file=sys.stderr,
    )
    deadline = time.monotonic() + grace_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(1.0, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ready, _, _ = probe_health_at(
            base_url,
            retries=1,
            timeout=min(probe_timeout, remaining),
            gap=0.0,
        )
        if ready:
            print("cc: agent-lb is ready — continuing with local LB", file=sys.stderr)
            return True


def lb_candidates() -> list[tuple[str, str]]:

    local_url = (os.environ.get("CLAUDE_LB_LOCAL_URL") or LOCAL_LB_URL).rstrip("/")
    remote_url = (os.environ.get("CLAUDE_LB_BASE_URL") or default_remote_url()).rstrip("/")
    prefer_local = os.environ.get("CLAUDE_LB_LOCAL_PREFER") != "0" and not remote_only_mac()
    prefer_remote = os.environ.get("CLAUDE_LB_PREFER_REMOTE") == "1"

    candidates: list[tuple[str, str]] = []
    if prefer_remote and remote_url != local_url:
        candidates.append(("remote", remote_url))
    if prefer_local:
        candidates.append(("local", local_url))
    if remote_url != local_url and all(url != remote_url for _, url in candidates):
        candidates.append(("remote", remote_url))
    if not candidates:
        candidates.append(("remote", remote_url))
    return candidates
