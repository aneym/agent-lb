from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
from starlette.websockets import WebSocket

from app.core import shutdown as shutdown_state

logger = logging.getLogger(__name__)

RESTART_CLOSE_CODE = 1012
RESTART_CLOSE_REASON = "agent-lb restarting; reconnect"
IDLE_CLOSE_CODE = 1001
IDLE_CLOSE_REASON = "Idle downstream websocket timeout"


class DownstreamActivity(Protocol):
    last_activity_at: float

    def mark_disconnected(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DownstreamClose:
    code: int
    reason: str
    streaming: bool = False


def draining() -> bool:
    return shutdown_state.websockets_closing() or not shutdown_state.admits_requests()


async def due(
    pending_requests: deque[Any],
    *,
    pending_lock: anyio.Lock,
    activity: DownstreamActivity,
    idle_timeout_seconds: float | None = None,
) -> DownstreamClose | None:
    async with pending_lock:
        streaming = bool(pending_requests)
    if shutdown_state.websockets_closing():
        return DownstreamClose(RESTART_CLOSE_CODE, RESTART_CLOSE_REASON, streaming=streaming)
    if streaming:
        return None
    if not shutdown_state.admits_requests():
        return DownstreamClose(RESTART_CLOSE_CODE, RESTART_CLOSE_REASON)
    if idle_timeout_seconds is not None and time.monotonic() - activity.last_activity_at >= idle_timeout_seconds:
        return DownstreamClose(IDLE_CLOSE_CODE, IDLE_CLOSE_REASON)
    return None


async def close_if_due(
    websocket: WebSocket,
    pending_requests: deque[Any],
    *,
    pending_lock: anyio.Lock,
    activity: DownstreamActivity,
    idle_timeout_seconds: float | None = None,
) -> bool:
    frame = await due(
        pending_requests,
        pending_lock=pending_lock,
        activity=activity,
        idle_timeout_seconds=idle_timeout_seconds,
    )
    if frame is None:
        return False
    if frame.code == RESTART_CLOSE_CODE:
        logger.info(
            "downstream_websocket_drain_close code=%s streaming=%s grace_expired=%s",
            frame.code,
            str(frame.streaming).lower(),
            str(shutdown_state.websockets_closing()).lower(),
        )
        activity.mark_disconnected()
    try:
        await websocket.close(code=frame.code, reason=frame.reason)
    except Exception:
        logger.debug("Failed to close downstream websocket", exc_info=True)
    return True
