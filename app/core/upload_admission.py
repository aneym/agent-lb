from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from typing import AsyncIterator, Mapping

from app.core import upload_throttle

logger = logging.getLogger(__name__)
OUTSTANDING_SECONDS = 4.0
QUANTUM_BYTES = 256 * 1024
HIGH_SHARE = 0.8


class UploadAdmissionTimeout(Exception):
    pass


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def upload_class(headers: Mapping[str, str]) -> str:
    priority = _header_value(headers, "x-agent-lb-priority")
    if isinstance(priority, str) and priority.strip().lower() in {"high", "batch"}:
        return priority.strip().lower()
    if _header_value(headers, "x-claude-code-agent-id") is not None:
        return "batch"
    user_agent = _header_value(headers, "user-agent")
    if not isinstance(user_agent, str):
        user_agent = ""
    if "sdk-cli" in user_agent or "workload/" in user_agent:
        return "batch"
    if user_agent.startswith("claude-cli/") and "(external, cli" in user_agent:
        return "high"
    return "batch"


def _normalize_class(upload_class: str) -> str:
    return "high" if upload_class == "high" else "batch"


@dataclass
class _Waiter:
    nbytes: int
    deadline: float
    upload_class: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    admitted: bool = False
    tracked: bool = False


@dataclass
class _Flow:
    queue: deque[_Waiter] = field(default_factory=deque)
    deficit: int = 0


class Admission:
    def __init__(self) -> None:
        self._outstanding = {"high": 0, "batch": 0}
        self._flows: dict[tuple[str, object], _Flow] = {}
        self._last_served: dict[str, tuple[str, object] | None] = {"high": None, "batch": None}
        self._last_any: tuple[str, object] | None = None

    def _within_budget(self, upload_class: str, nbytes: int, budget: float) -> bool:
        if upload_class == "high":
            outstanding = self._outstanding["high"]
            return outstanding == 0 or outstanding + nbytes <= HIGH_SHARE * budget
        if self._outstanding["batch"] == 0:
            return True
        room = budget - min(self._outstanding["high"], HIGH_SHARE * budget)
        return self._outstanding["batch"] + nbytes <= room

    def _keys(self, upload_class: str | None) -> list[tuple[str, object]]:
        if upload_class is None:
            keys = list(self._flows)
            last = self._last_any
        else:
            keys = [key for key in self._flows if key[0] == upload_class]
            last = self._last_served[upload_class]
        if last in keys:
            start = keys.index(last) + 1
            keys = keys[start:] + keys[:start]
        return keys

    def _high_admissible(self, budget: float) -> bool:
        now = time.monotonic()
        for key, flow in self._flows.items():
            if key[0] != "high" or not flow.queue:
                continue
            waiter = flow.queue[0]
            if now < waiter.deadline and self._within_budget("high", waiter.nbytes, budget):
                return True
        return False

    def _signal_expired(self) -> None:
        now = time.monotonic()
        for flow in self._flows.values():
            if flow.queue and now >= flow.queue[0].deadline:
                flow.queue[0].event.set()

    def _serve(self, upload_class: str | None, *, active: bool, budget: float) -> bool:
        keys = self._keys(upload_class)
        if not keys:
            return False
        last = self._last_any if upload_class is None else self._last_served[upload_class]
        now = time.monotonic()
        other_fits = active and any(
            key != last
            and now < self._flows[key].queue[0].deadline
            and self._within_budget(key[0], self._flows[key].queue[0].nbytes, budget)
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
                if not self._within_budget(key[0], waiter.nbytes, budget):
                    continue
                fits = True
                if key == last and other_fits:
                    continue
                if flow.deficit < waiter.nbytes:
                    continue
                flow.deficit -= waiter.nbytes
                self._outstanding[key[0]] += waiter.nbytes
                waiter.tracked = True
            flow.queue.popleft()
            del self._flows[key]
            if flow.queue:
                self._flows[key] = flow
            self._last_served[key[0]] = key
            self._last_any = key
            waiter.admitted = True
            waiter.event.set()
            return True
        return fits  # Large heads earn enough credit across rounds.

    def _drain(self) -> None:
        enabled, rate = upload_throttle.read_state()
        active = enabled and os.environ.get("AGENT_LB_UPLOAD_FAIR", "1") != "0"
        budget = max(rate * OUTSTANDING_SECONDS, 1)
        while self._flows:
            self._signal_expired()
            if not active:
                if not self._serve(None, active=False, budget=budget):
                    return
                continue
            if self._high_admissible(budget) and self._serve("high", active=True, budget=budget):
                continue
            if not self._serve("batch", active=True, budget=budget):
                return

    @asynccontextmanager
    async def admit(
        self,
        session_key: str | None,
        nbytes: int,
        upload_class: str = "batch",
    ) -> AsyncIterator[None]:
        enabled, _ = upload_throttle.read_state()
        if not enabled or os.environ.get("AGENT_LB_UPLOAD_FAIR", "1") == "0":
            yield
            return
        max_wait = float(os.environ.get("AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS", "180"))
        started = time.monotonic()
        cls = _normalize_class(upload_class)
        key = (cls, session_key if session_key is not None else object())
        waiter = _Waiter(max(nbytes, 0), started + max_wait, cls)
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
                    "Upload admission session=%s class=%s bytes=%s wait_seconds=%.3f",
                    sha256((session_key or "anonymous").encode()).hexdigest()[:12],
                    cls,
                    nbytes,
                    waited,
                )
            yield
        finally:
            if waiter.tracked:
                self._outstanding[waiter.upload_class] -= waiter.nbytes
            if not waiter.admitted:
                flow.queue.remove(waiter)
                if not flow.queue:
                    self._flows.pop(key, None)
            self._drain()


_admission = Admission()


def admission() -> Admission:
    return _admission
