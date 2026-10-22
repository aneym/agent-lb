from __future__ import annotations

import datetime as dt
import os
import pwd
import re
import shutil
import sys
from pathlib import Path

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


LB_HOME = REAL_HOME / ".agent-lb"


SANDBOXES = LB_HOME / "sandboxes"


VOLUME_SIZE = "2g"


LIVE_RUNTIME = LB_HOME / "runtime" / "agent-lb"


LIVE_VENV = LIVE_RUNTIME / ".venv"


LIVE_PLIST = REAL_HOME / "Library" / "LaunchAgents" / "com.aneyman.agent-lb.plist"


LIVE_LABEL = "com.aneyman.agent-lb"


LIVE_FRONT_LABEL = "com.aneyman.agent-lb-front"


LIVE_FRONT_STATE = LB_HOME / "state" / "front.json"


LIVE_FRONT_PORT = 2455


LIVE_PRIMARY_PORT = 2457


LIVE_PEER_URL = "http://127.0.0.1:2455"


CODEX_KEY_DIR = REAL_HOME / ".codex-lb"


MANAGED_ROUTING = LB_HOME / "managed" / "coding-agents"


LABEL_PREFIX = "com.agent-lb.drill."


FORBIDDEN_PORTS = frozenset({1455, 2455, 2457, 2459})


MIN_PORT = 2470


PICK_RANGE = range(2470, 2600)


RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


TOKEN_ENV = "AGENT_LB_FEDERATION_TOKEN"


EDGES = {"anthropic": "https://api.anthropic.com", "openai": "https://chatgpt.com/backend-api"}


GATE_ALLOWED = frozenset({("GET", "/api/federation/mirror"), ("GET", "/api/federation/status")})


VENDOR_OF_PROVIDER = {"anthropic": "anthropic", "openai": "openai"}


USABLE_EXPIRY_S = 1800


DRAIN_S = 120


HOLD_MAX_S = 120


CUSTODY_FILES = (
    "data/store.db",
    "data/store.db-wal",
    "data/store.db-shm",
    "data/store.db-journal",
    "data/encryption.key",
)


ENV_MARKER = "LB_SANDBOX_ROOT="


EXIT_OK, EXIT_ASSERT, EXIT_REFUSED, EXIT_UNHEALTHY = 0, 1, 2, 3


DISABLED_SCHEDULERS = (
    "METRICS",
    "USAGE_REFRESH",
    "MODEL_REGISTRY",
    "ACCOUNT_PULSE",
    "RESET_CREDIT_AUTO_REDEEM",
    "RESET_CREDIT_EXPIRY_REDEEM",
    "ACCOUNTS_CACHE_WARMER",
    "STICKY_SESSION_CLEANUP",
    "QUOTA_PLANNER_SCHEDULER",
    "HTTP_RESPONSES_SESSION_BRIDGE",
    "AUTH_GUARDIAN",
)


class Refused(Exception):
    pass


class Unhealthy(Exception):
    pass


RESERVED_RUN_IDS = frozenset(
    {"state", "data", "bin", "runtime", "logs", "sandboxes", "managed", "backups", "shims", "tls", "team", "seats"}
)


def check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise Refused(f"run id {run_id!r} must match {RUN_ID_RE.pattern}")
    if run_id.endswith("-aux"):
        raise Refused(f"run id {run_id!r} must not end in -aux")
    if run_id in RESERVED_RUN_IDS or os.path.lexists(LB_HOME / run_id):
        raise Refused(f"run id {run_id!r} names a live agent-lb directory")
    return run_id


def check_label(label: str) -> str:
    suffix = label.removeprefix(LABEL_PREFIX)
    if not label.startswith(LABEL_PREFIX) or not suffix or not suffix.replace("-", "").isalnum():
        raise Refused(f"label {label!r} is not a {LABEL_PREFIX}* drill label")
    return label


def check_ports(ports: list[int]) -> list[int]:
    for port in ports:
        if isinstance(port, bool) or not isinstance(port, int) or port < MIN_PORT or port > 2599:
            raise Refused(f"port {port!r} is outside {MIN_PORT}-2599")
        if port in FORBIDDEN_PORTS:
            raise Refused(f"port {port} belongs to the live agent-lb")
    if len(set(ports)) != len(ports):
        raise Refused("sandbox ports repeat")
    return ports


NATIVE_MAGIC = (
    (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")
    if sys.platform == "darwin"
    else (b"\x7fELF",)
)


BASE_INTERPRETER_NAME = re.compile(r"^python(3(\.[0-9]+)?)?$")


def bootstrap_script() -> Path:
    return Path(__file__).resolve().parents[int(Path(__file__).suffix == ".py")] / "lb-sandbox"


def check_launchctl() -> None:
    found = shutil.which("launchctl")
    if found != "/bin/launchctl":
        raise Refused(f"launchctl resolves to {found}, not /bin/launchctl")


def labels_for(run_id: str) -> tuple[str, str]:
    check_run_id(run_id)
    label = check_label(f"{LABEL_PREFIX}sbx-{run_id}")
    return label, check_label(f"{label}-aux")


def root_for(run_id: str) -> Path:
    return SANDBOXES / check_run_id(run_id)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Confinement(OSError):
    pass
