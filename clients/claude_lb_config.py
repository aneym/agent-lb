from __future__ import annotations  # noqa: I001


import os
import re
import sys
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
    return STUDIO_LB_URL if remote_only_mac() else LOCAL_LB_URL


ENTRY_PATH = Path(__file__).resolve().with_name("claude-lb-launch")


class LaunchContext:
    base_url = (os.environ.get("CLAUDE_LB_BASE_URL") or default_remote_url()).rstrip("/")


launch_context = LaunchContext()


CCGPT_MODE = os.environ.get("CLAUDE_LB_CODEX_MODE") == "1"


CCGPT_MODEL = "sol-latest"


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


SHIM_READY_FILE_MAX_AGE_SECONDS = 24 * 3600


SHIM_READY_FILE_DEAD_GRACE_SECONDS = 10


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


WAIT_BUFFER_SECONDS = 20


RESUME_MAX_ATTEMPTS = 5
