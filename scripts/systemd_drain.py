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
WEBSOCKET_CLOSE_WAIT_SECONDS = 5.0


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


def _status(base: str) -> tuple[int, int | None]:
    with urllib.request.urlopen(f"{base}/internal/drain/status", timeout=5) as resp:
        checks = json.load(resp)["checks"]
    admitting = 1 if checks.get("admitting") == "true" else 0
    websockets = checks.get("websockets")
    return int(checks["in_flight"]) + admitting, None if websockets is None else int(websockets)


def _close_websockets(base: str, log: Callable[[str], None], websockets: int) -> None:
    try:
        _post(f"{base}/internal/drain/close-websockets")
    except OSError as exc:
        log(f"websocket close request failed: {exc}")
        return
    deadline = time.monotonic() + WEBSOCKET_CLOSE_WAIT_SECONDS
    left: int | None = websockets
    while left and time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            left = _status(base)[1]
        except (OSError, ValueError, KeyError):
            break
    log(f"closed {websockets} websocket(s) for restart with code 1012; {left} still open")


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
    websockets: int | None = None
    peak = 0
    while time.monotonic() < deadline:
        try:
            left, websockets = _status(base)
        except (OSError, ValueError, KeyError) as exc:
            log(f"drain status unreadable, restarting: {exc}")
            return
        peak = max(peak, websockets or 0)
        if left <= 0:
            seen = "websockets unreported" if websockets is None else f"websockets peak {peak}, cut 0"
            log(f"drained in {time.monotonic() - started:.0f}s ({seen})")
            return
        time.sleep(POLL_SECONDS)
    log(f"drain budget spent with {left} in flight ({websockets} websocket(s)), restarting")
    if websockets:
        _close_websockets(base, log, websockets)


def undrain(health_url: str) -> None:
    try:
        _post(f"{_base(health_url)}/internal/drain/stop")
    except OSError:
        pass
