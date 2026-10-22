from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path


class RestartContext:
    HOME = Path.home()

    LB_HOME = HOME / ".agent-lb"

    RUNTIME = LB_HOME / "runtime" / "agent-lb"

    BACKUPS = LB_HOME / "runtime" / "backups"

    SYNC_LOG = LB_HOME / "runtime" / "sync.log"

    STATE_DIR = LB_HOME / "state"

    LOCK_FILE = LB_HOME / "runtime" / "lb-restart.lock"

    PREFERRED_FILE = STATE_DIR / "front-preferred-port"

    FRONT_STATE = STATE_DIR / "front.json"

    STANDBY_PIDFILE = STATE_DIR / "lb-standby.pid"

    STANDBY_LOG = LB_HOME / "agent-lb-standby.log"

    WATCHDOG_PAUSE = LB_HOME / "watchdog.pause"

    LABEL = "com.aneyman.agent-lb"

    FRONT_LABEL = "com.aneyman.agent-lb-front"

    PLIST = HOME / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    FRONT_PORT = 2455

    PRIMARY_PORT = 2457

    STANDBY_PORT = 2459

    UID = os.getuid()

    LSOF = shutil.which("lsof") or "/usr/sbin/lsof"

    SANDBOX_LABEL_PREFIX = "com.agent-lb.drill."

    SANDBOX_FORBIDDEN_PORTS = frozenset({1455, 2455, 2457, 2459})

    SANDBOX_MIN_PORT = 2470

    SANDBOX_KEYS = ("root", "label", "plist", "runtime", "state_dir", "front_port", "primary_port", "standby_port")

    SANDBOX_CONFIG: Path | None = None

    SANDBOX_ROOT: Path | None = None

    SANDBOX_PORT_URL = re.compile("^http://127\\.0\\.0\\.1:([0-9]{4})$")

    SANDBOX_LEADING_PORT = re.compile("^([0-9]{1,5})\\b")

    SANDBOX_PLIST_KEYS = frozenset(
        {
            "Label",
            "ProgramArguments",
            "WorkingDirectory",
            "EnvironmentVariables",
            "RunAtLoad",
            "KeepAlive",
            "ThrottleInterval",
            "ExitTimeOut",
            "StandardOutPath",
            "StandardErrorPath",
        }
    )

    NATIVE_MAGIC = (
        (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")
        if sys.platform == "darwin"
        else (b"\x7fELF",)
    )

    BASE_INTERPRETER_NAME = re.compile("^python(3(\\.[0-9]+)?)?$")

    SANDBOX_ENV_KEYS = frozenset(
        {"HOME", "PATH", "PYTHONPATH", "LB_SANDBOX_ROOT", "FORWARDED_ALLOW_IPS", "UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN"}
    )

    SANDBOX_ENV_PREFIX = "AGENT_LB_"

    SANDBOX_PATH_ENV = "/usr/bin:/bin:/usr/sbin:/sbin"

    SANDBOX_CONFIG_NAME = "lb-restart.json"

    SANDBOX_CONFIG_MAX = 1 << 16

    SANDBOX_ROOT_FD: int | None = None

    SANDBOX_ROOT_PINNED: tuple[Path, int, int] | None = None

    T0 = time.monotonic()

    OAUTH_POLL_S = 2.0

    REAP_MARGIN_S = 150

    JOB_ENV_BLOCKS = ("environment", "inherited environment", "default environment")

    LAUNCHD_ENV_KEYS = frozenset({"XPC_SERVICE_NAME", "OSLogRateLimit", "XPC_FLAGS"})

    JOB_STREAMS = {"stdout path": "StandardOutPath", "stderr path": "StandardErrorPath", "stdin path": "StandardInPath"}

    JOB_FLAG_LINES = frozenset({"submitted job. ignore execute allowed"})

    def __init__(self, entry: Path) -> None:
        self.entry = entry
        self.LIVE_BINDINGS = {
            name: getattr(self, name)
            for name in (
                "SANDBOX_ROOT",
                "RUNTIME",
                "BACKUPS",
                "SYNC_LOG",
                "STATE_DIR",
                "LOCK_FILE",
                "PREFERRED_FILE",
                "FRONT_STATE",
                "STANDBY_PIDFILE",
                "STANDBY_LOG",
                "WATCHDOG_PAUSE",
                "LABEL",
                "FRONT_LABEL",
                "PLIST",
                "FRONT_PORT",
                "PRIMARY_PORT",
                "STANDBY_PORT",
            )
        }


class SandboxRefused(Exception):
    pass
