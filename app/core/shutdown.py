from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Collection
from typing import TypeVar

from app.core.shutdown_budget import EXCHANGE_SETTLE_S as EXCHANGE_SETTLE_S

_TaskResultT = TypeVar("_TaskResultT")
DRAIN_DEADLINE_HEADER = "x-codex-lb-drain-deadline-monotonic"
_draining: bool = False
_drain_deadline: float | None = None
_drain_started: bool = False
_shutdown_committed: bool = False
_shutdown_deadline_candidates: list[float] = []
_server_start_pending: bool = False
_lifespan_completed: bool = False
_bridge_drain_active: bool = False
_in_flight: int = 0
_control_plane_task_admission_open: bool = True


def _effective_drain_deadline() -> float | None:
    candidates = _shutdown_deadline_candidates
    if _drain_deadline is None:
        return min(candidates) if candidates else None
    if not candidates:
        return _drain_deadline
    return min(_drain_deadline, *candidates)


_exchanges: int = 0


def reset() -> None:
    global _draining, _bridge_drain_active, _in_flight, _exchanges
    global _drain_deadline, _drain_started, _shutdown_committed, _shutdown_deadline_candidates
    global _server_start_pending, _lifespan_completed, _control_plane_task_admission_open
    _control_plane_task_admission_open = True
    _draining = False
    _drain_deadline = None
    _drain_started = False
    _shutdown_committed = False
    _shutdown_deadline_candidates = []
    _server_start_pending = False
    _lifespan_completed = False
    _bridge_drain_active = False
    _in_flight = 0
    _exchanges = 0


def prepare_server_start() -> None:
    global _server_start_pending
    reset()
    _server_start_pending = True


def prepare_lifespan_start() -> None:
    global _server_start_pending
    if _server_start_pending:
        _server_start_pending = False
        return
    if _lifespan_completed:
        reset()


def mark_lifespan_completed() -> None:
    global _server_start_pending, _lifespan_completed
    _server_start_pending = False
    _lifespan_completed = True


def close_control_plane_task_admission() -> None:
    global _control_plane_task_admission_open
    _control_plane_task_admission_open = False


def is_control_plane_task_admission_open() -> bool:
    return _control_plane_task_admission_open


def set_draining(val: bool = True) -> None:
    global _draining
    if val:
        _draining = True
        return
    stop_drain()


def _new_drain_deadline_candidate(timeout_seconds: float, *, deadline_monotonic: float | None) -> float:
    requested_deadline: float | None = None
    if deadline_monotonic is not None:
        requested_deadline = float(deadline_monotonic)
        if not math.isfinite(requested_deadline):
            raise ValueError("drain deadline must be finite")
    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    if requested_deadline is not None:
        deadline = min(deadline, requested_deadline)
    return deadline


def begin_drain(timeout_seconds: float, *, deadline_monotonic: float | None = None) -> float:
    global _draining, _drain_deadline, _drain_started, _bridge_drain_active
    global _shutdown_deadline_candidates
    if _drain_started and deadline_monotonic is None:
        _draining = True
        _bridge_drain_active = True
        deadline = _effective_drain_deadline()
        assert deadline is not None
        return deadline
    deadline = _new_drain_deadline_candidate(timeout_seconds, deadline_monotonic=deadline_monotonic)
    if _drain_deadline is None or deadline < _drain_deadline:
        _drain_deadline = deadline
    _draining = True
    _bridge_drain_active = True
    _drain_started = True
    deadline = _effective_drain_deadline()
    assert deadline is not None
    if _shutdown_committed:
        _shutdown_deadline_candidates.append(deadline)
    return deadline


def commit_shutdown(timeout_seconds: float, *, deadline_monotonic: float | None = None) -> float:
    global _draining, _drain_started, _shutdown_committed
    global _shutdown_deadline_candidates
    global _bridge_drain_active
    if _shutdown_committed and deadline_monotonic is None:
        deadline = _effective_drain_deadline()
        assert deadline is not None
        return deadline
    deadline = _new_drain_deadline_candidate(timeout_seconds, deadline_monotonic=deadline_monotonic)
    _shutdown_deadline_candidates.append(deadline)
    if _drain_deadline is not None:
        _shutdown_deadline_candidates.append(_drain_deadline)
    _shutdown_committed = True
    _draining = True
    _bridge_drain_active = True
    _drain_started = True
    effective_deadline = _effective_drain_deadline()
    assert effective_deadline is not None
    return effective_deadline


def is_shutdown_committed() -> bool:
    return _shutdown_committed


def stop_drain() -> bool:
    global _draining, _drain_deadline, _drain_started, _bridge_drain_active
    if _shutdown_committed:
        return False
    _drain_started = False
    _draining = False
    _drain_deadline = None
    _bridge_drain_active = False
    if _shutdown_committed:
        assert _shutdown_deadline_candidates
        _drain_started = True
    return not _shutdown_committed


def remaining_drain_timeout_seconds() -> float | None:
    deadline = _effective_drain_deadline()
    if deadline is None:
        return None
    return max(deadline - time.monotonic(), 0.0)


def is_draining() -> bool:
    return _draining or _shutdown_committed


def set_bridge_drain_active(val: bool = True) -> None:
    global _bridge_drain_active
    _bridge_drain_active = val


def is_bridge_drain_active() -> bool:
    return _bridge_drain_active or _shutdown_committed


def increment_in_flight() -> None:
    global _in_flight
    _in_flight += 1


def decrement_in_flight() -> None:
    global _in_flight
    _in_flight = max(0, _in_flight - 1)


def get_in_flight() -> int:
    return _in_flight


async def wait_for_in_flight_drain(
    timeout_seconds: float, poll_interval_seconds: float = 0.1, should_abort: Callable[[], bool] | None = None
) -> bool:
    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    shared_deadline = _effective_drain_deadline()
    if shared_deadline is not None:
        deadline = min(deadline, shared_deadline)
    while get_in_flight() > 0:
        if should_abort is not None and should_abort():
            return False
        shared_deadline = _effective_drain_deadline()
        if shared_deadline is not None:
            deadline = min(deadline, shared_deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(poll_interval_seconds, remaining))
    return get_in_flight() == 0


async def wait_for_tasks_to_drain(
    tasks: Collection[asyncio.Task[_TaskResultT]], timeout_seconds: float
) -> set[asyncio.Task[_TaskResultT]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        pending = {task for task in tasks if not task.done()}
        if not pending:
            await asyncio.sleep(0)
            pending = {task for task in tasks if not task.done()}
            if not pending:
                return set()
        remaining = deadline - loop.time()
        if remaining <= 0:
            return pending
        _, still_pending = await asyncio.wait(pending, timeout=remaining)
        if still_pending:
            return {task for task in tasks if not task.done()}


def begin_exchange() -> None:
    global _exchanges
    _exchanges += 1


def end_exchange() -> None:
    global _exchanges
    _exchanges = max(0, _exchanges - 1)


def get_exchanges() -> int:
    return _exchanges


async def settle_exchanges(timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while get_exchanges() > 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    return get_exchanges() == 0
