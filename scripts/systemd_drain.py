from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

FRONT_UNIT = "agent-lb-front"
FRONT_SCRIPT = Path("scripts/agent-lb-front.mjs")
POLL_SECONDS = 2.0


def budget_seconds() -> float:
    return float(os.environ.get("AGENT_LB_FOLLOW_DRAIN_SECONDS", "600"))


def front_digest(runtime: Path) -> str:
    try:
        return hashlib.sha256((runtime / FRONT_SCRIPT).read_bytes()).hexdigest()
    except OSError:
        return ""


def units_to_restart(units: list[str], front_before: str, front_after: str) -> list[str]:
    if front_before and front_before == front_after:
        return [unit for unit in units if unit.removesuffix(".service") != FRONT_UNIT]
    return units


def _base(health_url: str) -> str:
    parts = urlsplit(health_url)
    return f"{parts.scheme}://{parts.netloc}"


def _post(url: str) -> None:
    with urllib.request.urlopen(urllib.request.Request(url, data=b"", method="POST"), timeout=5):
        pass


def _in_flight(base: str) -> int:
    with urllib.request.urlopen(f"{base}/internal/drain/status", timeout=5) as resp:
        checks = json.load(resp)["checks"]
    admitting = 1 if checks.get("admitting") == "true" else 0
    return int(checks["in_flight"]) + admitting


def drain(health_url: str, log: Callable[[str], None], budget: float | None = None) -> None:
    base = _base(health_url)
    try:
        _post(f"{base}/internal/drain/start")
    except OSError as exc:
        log(f"drain start failed, restarting without drain: {exc}")
        return
    started = time.monotonic()
    deadline = started + (budget_seconds() if budget is None else budget)
    left: int | None = None
    while time.monotonic() < deadline:
        try:
            left = _in_flight(base)
        except (OSError, ValueError, KeyError) as exc:
            log(f"drain status unreadable, restarting: {exc}")
            return
        if left <= 0:
            log(f"drained in {time.monotonic() - started:.0f}s")
            return
        time.sleep(POLL_SECONDS)
    log(f"drain budget spent with {left} request(s) in flight, restarting")


def undrain(health_url: str) -> None:
    try:
        _post(f"{_base(health_url)}/internal/drain/stop")
    except OSError:
        pass
