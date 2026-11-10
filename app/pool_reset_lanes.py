from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from typing import Any

HOST = "ax102"
PATH = "/home/jobs/.agent-rails/factory/pool-pacing/lane-order.json"
SCHEMA = "pool-pacing-lane-order/1"


def default_source() -> str:
    return PATH if socket.gethostname().split(".")[0] == HOST else f"{HOST}:{PATH}"


def _fetch(spec: str) -> str:
    host, sep, path = spec.partition(":")
    if not sep or spec.startswith("/"):
        return Path(spec).read_text()
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "cat", path]
    done = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise OSError(f"ssh {host} cat exited {done.returncode}")
    return done.stdout


def read(spec: str) -> dict[str, Any]:
    found: dict[str, Any] = {"source": spec, "lanes": []}
    try:
        data = json.loads(_fetch(spec))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {**found, "why": f"the list could not be read: {type(exc).__name__}"}
    lanes = data.get("lanes") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(lanes, list):
        return {**found, "why": f"the list is not a {SCHEMA} file with a lanes list"}
    named = [lane for lane in lanes if isinstance(lane, str) and lane.strip()]
    if not named:
        return {**found, "why": "the list names no lane"}
    return {
        **found,
        "lanes": named,
        "why": f"{len(named)} named",
        "since": data.get("since"),
        "set_by": data.get("set_by"),
    }
