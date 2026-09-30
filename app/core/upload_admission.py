from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from typing import AsyncIterator

from app.core import upload_throttle

logger = logging.getLogger(__name__)
OUTSTANDING_SECONDS = 4.0
QUANTUM_BYTES = 256 * 1024


class UploadAdmissionTimeout(Exception):
    pass


@dataclass
class _Waiter:
    nbytes: int
    deadline: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    admitted: bool = False
    tracked: bool = False


@dataclass
class _Flow:
    queue: deque[_Waiter] = field(default_factory=deque)
    deficit: int = 0


class Admission:
    def __init__(self) -> None:
        self._outstanding = 0
        self._flows: dict[object, _Flow] = {}
        self._last_served: object | None = None

    def _drain(self) -> None:
        enabled, rate = upload_throttle.read_state()
        active = enabled and os.environ.get("AGENT_LB_UPLOAD_FAIR", "1") != "0"
        budget = max(rate * OUTSTANDING_SECONDS, 1)
        while self._flows:
            keys = list(self._flows)
            if self._last_served in keys:
                start = keys.index(self._last_served) + 1
                keys = keys[start:] + keys[:start]
            now = time.monotonic()
            other_fits = active and any(
                key != self._last_served
                and now < self._flows[key].queue[0].deadline
                and (not self._outstanding or self._outstanding + self._flows[key].queue[0].nbytes <= budget)
                for key in keys
            )
            fits = False
            for key in keys:
                flow = self._flows[key]
                waiter = flow.queue[0]
                if time.monotonic() >= waiter.deadline:
                    waiter.event.set()
                    continue
                if active:
                    flow.deficit += QUANTUM_BYTES
                    if self._outstanding and self._outstanding + waiter.nbytes > budget:
                        continue
                    fits = True
                    if key == self._last_served and other_fits:
                        continue
                    if flow.deficit < waiter.nbytes:
                        continue
                    flow.deficit -= waiter.nbytes
                    self._outstanding += waiter.nbytes
                    waiter.tracked = True
                flow.queue.popleft()
                del self._flows[key]
                if flow.queue:
                    self._flows[key] = flow
                self._last_served = key
                waiter.admitted = True
                waiter.event.set()
                break
            else:
                if fits:
                    continue  # Large heads earn enough credit across rounds.
                return

    @asynccontextmanager
    async def admit(self, session_key: str | None, nbytes: int) -> AsyncIterator[None]:
        enabled, _ = upload_throttle.read_state()
        if not enabled or os.environ.get("AGENT_LB_UPLOAD_FAIR", "1") == "0":
            yield
            return
        max_wait = float(os.environ.get("AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS", "180"))
        started = time.monotonic()
        key = session_key if session_key is not None else object()
        waiter = _Waiter(max(nbytes, 0), started + max_wait)
        flow = self._flows.setdefault(key, _Flow())
        flow.queue.append(waiter)
        try:
            while not waiter.admitted:
                self._drain()
                if waiter.admitted:
                    break
                remaining = max_wait - (time.monotonic() - started)
                if remaining <= 0:
                    raise UploadAdmissionTimeout()
                try:
                    await asyncio.wait_for(waiter.event.wait(), timeout=min(1.0, remaining))
                except asyncio.TimeoutError:
                    pass
            waited = time.monotonic() - started
            if waited > 5:
                logger.info(
                    "Upload admission session=%s bytes=%s wait_seconds=%.3f",
                    sha256((session_key or "anonymous").encode()).hexdigest()[:12],
                    nbytes,
                    waited,
                )
            yield
        finally:
            if waiter.tracked:
                self._outstanding -= waiter.nbytes
            if not waiter.admitted:
                flow.queue.remove(waiter)
                if not flow.queue:
                    self._flows.pop(key, None)
            self._drain()


_admission = Admission()


def admission() -> Admission:
    return _admission
