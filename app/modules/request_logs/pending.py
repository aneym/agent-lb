from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_pending_tasks: set[asyncio.Task[None]] = set()
_tasks_by_request_id: dict[str, set[asyncio.Task[None]]] = {}


def track_request_log_task(task: asyncio.Task[None], *, request_id: str | None = None) -> None:
    _pending_tasks.add(task)
    key = request_id.strip() if isinstance(request_id, str) else ""
    if key:
        _tasks_by_request_id.setdefault(key, set()).add(task)

    def _done(done_task: asyncio.Task[None]) -> None:
        _pending_tasks.discard(done_task)
        if key:
            bucket = _tasks_by_request_id.get(key)
            if bucket is not None:
                bucket.discard(done_task)
                if not bucket:
                    _tasks_by_request_id.pop(key, None)
        try:
            done_task.result()
        except asyncio.CancelledError:
            logger.warning("Request log persistence task cancelled request_id=%s", key or None)
        except Exception as exc:
            logger.warning(
                "Request log persistence task failed request_id=%s",
                key or None,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    task.add_done_callback(_done)


async def wait_for_request_log(request_id: str | None, *, timeout_seconds: float = 2.0) -> None:
    key = request_id.strip() if isinstance(request_id, str) else ""
    if not key:
        return
    tasks = list(_tasks_by_request_id.get(key, ()))
    if not tasks:
        return
    _, remaining = await asyncio.wait(tasks, timeout=timeout_seconds)
    if remaining:
        logger.warning("Timed out waiting for pending request log request_id=%s", key)


async def drain_request_log_tasks(*, timeout_seconds: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while _pending_tasks:
        remaining_time = deadline - loop.time()
        if remaining_time <= 0:
            logger.warning("Shutdown timed out with %d request log tasks pending", len(_pending_tasks))
            return
        pending = list(_pending_tasks)
        _, still_pending = await asyncio.wait(pending, timeout=remaining_time)
        if still_pending:
            logger.warning("Shutdown timed out with %d request log tasks pending", len(still_pending))
            return
