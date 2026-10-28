from __future__ import annotations

import os
import re
import resource
import select
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LOCAL_LB_URL = "http://127.0.0.1:2455"


STUDIO_LB_URL = "https://studio.tailf266ac.ts.net:2455"


def remote_only_mac() -> bool:
    if sys.platform != "darwin" or os.environ.get("CLAUDE_LB_LOCAL_URL"):
        return False
    label = os.environ.get("CLAUDE_LB_LAUNCHD_LABEL") or "com.aneyman.agent-lb"
    return not (Path.home() / "Library" / "LaunchAgents" / f"{label}.plist").exists()


def default_remote_url() -> str:
    if remote_only_mac() or sys.platform.startswith("linux"):
        return STUDIO_LB_URL
    return LOCAL_LB_URL


class LaunchConfig:
    base_url = (os.environ.get("CLAUDE_LB_BASE_URL") or default_remote_url()).rstrip("/")


def arg_value(argv: list[str], *names: str) -> str | None:
    for index, value in enumerate(argv):
        for name in names:
            if value == name and index + 1 < len(argv):
                return argv[index + 1]
            prefix = f"{name}="
            if value.startswith(prefix):
                return value[len(prefix) :]
    return None


MODE_FILE = Path.home() / ".config/agent-lb/top-level-mode"


TOP_LEVEL_MODE = MODE_FILE.read_text().strip() if MODE_FILE.exists() else "claude"


REQUESTED_MODEL = arg_value(sys.argv[1:], "--model", "-m") or os.environ.get("ANTHROPIC_MODEL", "")
CCGPT_MODE = (
    os.environ.get("CLAUDE_LB_CODEX_MODE") == "1"
    or os.environ.get("CLAUDE_LB_CODEX_MODE") != "0"
    and (TOP_LEVEL_MODE == "sol" or REQUESTED_MODEL.startswith("gpt-"))
)


CCGPT_MODEL = REQUESTED_MODEL if REQUESTED_MODEL.startswith("gpt-") else "sol-latest"


CCGPT_MODEL_NAME = re.compile(r"^(?:gpt-[\w.-]+|[a-z]+-latest(?:-(?:low|medium|high|xhigh))?)$")


DEFAULT_CLAUDE_MODEL = "opus[1m]"


DEFAULT_CLAUDE_EFFORT = "high"


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"


EXIT_TEMPFAIL = 75


SHIM_START_TIMEOUT_DEFAULT = 90.0


STALL_TIMEOUT_DEFAULT = 300.0


STALL_STARTUP_GRACE = 30.0


WORK_PROCESS_POLL_SECONDS = 30.0


SHIM_CONNECT_RETRIES_DEFAULT = 32


SHIM_CONNECT_BACKOFF_DEFAULT = 0.5


SHIM_CONNECT_BACKOFF_CAP_DEFAULT = 4.0


SHIM_CLIENT_IDLE_TIMEOUT_SECONDS = 600


SHIM_TUNNEL_RATE_MBPS_DEFAULT = 40.0


SHIM_TUNNEL_LOG_MIN_BYTES = 8 * 1024 * 1024


SHIM_TUNNEL_SLOW_CONNECT_SECONDS = 3.0


TLS_DIR = Path.home() / ".agent-lb" / "tls"


CA_CERT = TLS_DIR / "ca.pem"


CA_KEY = TLS_DIR / "ca-key.pem"


LEAF_CERT = TLS_DIR / "leaf.pem"


LEAF_KEY = TLS_DIR / "leaf-key.pem"


CERT_MARKER = TLS_DIR / ".v1"


INTERCEPT_HOSTS = {"api.anthropic.com"}


DIRECT_UPGRADE_HOST = "api.anthropic.com"


DIRECT_UPGRADE_ADDRESS = (DIRECT_UPGRADE_HOST, 443)


AGENT_LB_ANTHROPIC_PATHS = frozenset(
    {
        "/health",
        "/v1/messages",
        "/v1/messages/count_tokens",
    }
)


def direct_upgrade_tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def splice(
    a: socket.socket,
    b: socket.socket,
    bucket: "TunnelBucket | None" = None,
) -> tuple[int, int]:

    ends = (a, b)
    a_to_b = 0
    b_to_a = 0
    try:
        while True:
            readable, _, errored = select.select(ends, [], ends, 300)
            if errored:
                break
            if not readable:
                continue
            for src in readable:
                dst = b if src is a else a
                try:
                    data = src.recv(65536)
                    while getattr(src, "pending", None) is not None and src.pending():
                        data += src.recv(65536)
                except OSError:
                    return a_to_b, b_to_a
                if not data:
                    return a_to_b, b_to_a
                if bucket is not None:
                    bucket.throttle(len(data))
                try:
                    dst.sendall(data)
                except OSError:
                    return a_to_b, b_to_a
                if src is a:
                    a_to_b += len(data)
                else:
                    b_to_a += len(data)
    finally:
        for sock in ends:
            try:
                sock.close()
            except OSError:
                pass
    return a_to_b, b_to_a


def proxy_ready_path(session_id: str) -> Path:
    shim_dir = Path.home() / ".agent-lb" / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    try:
        shim_dir.chmod(0o700)
    except OSError:
        pass
    return shim_dir / f"{session_id}.proxy"


def activity_path(session_id: str, parent_pid: int) -> Path:
    return proxy_ready_path(session_id).parent / f"{session_id}.{parent_pid}.activity"


def client_activity_path(client_session_id: str) -> Path | None:
    if re.fullmatch(r"[A-Za-z0-9-]{1,128}", client_session_id) is None:
        return None
    return proxy_ready_path("x").parent / f"client-{client_session_id}.activity"


def raise_fd_soft_limit(target: int = 10240) -> None:

    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if hard != resource.RLIM_INFINITY:
            target = min(hard, target)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (OSError, ValueError):
        pass


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def is_retryable_shim_connect_error(error: BaseException) -> bool:

    if isinstance(error, urllib.error.HTTPError):
        return False
    reason = getattr(error, "reason", None)
    return isinstance(error, ConnectionError) or isinstance(reason, ConnectionError)


def has_permission_option(argv: list[str]) -> bool:
    return any(
        argument == "--permission-mode"
        or argument.startswith("--permission-mode=")
        or argument == "--dangerously-skip-permissions"
        for argument in argv
    )


def headless_invocation(argv: list[str]) -> bool:
    return "-p" in argv or "--print" in argv


class TunnelBucket:
    SMALL_WRITE_BYTES = 8192

    def __init__(self, rate_bytes_per_second: float, burst_bytes: float | None = None) -> None:
        self.rate = rate_bytes_per_second

        capacity = max(rate_bytes_per_second, 131072.0) if burst_bytes is None else burst_bytes
        self.burst_seconds = capacity / rate_bytes_per_second if rate_bytes_per_second > 0 else 0.0
        self.next_free = time.monotonic() - self.burst_seconds
        self.lock = threading.Lock()

    def throttle(self, nbytes: int) -> None:
        if self.rate <= 0:
            return
        with self.lock:
            now = time.monotonic()
            base = max(self.next_free, now - self.burst_seconds)
            wait = base - now
            self.next_free = base + nbytes / self.rate
        if nbytes <= self.SMALL_WRITE_BYTES and wait <= self.burst_seconds:
            return
        while wait > 0:
            step = min(wait, 0.5)
            time.sleep(step)
            wait -= step
