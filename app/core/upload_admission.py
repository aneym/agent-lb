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
    def __init__(
        self,
        *,
        rate: float,
        backlog_bytes: int,
        projected_wait_s: float,
        max_wait_s: float,
        upload_class: str,
        waited_s: float | None = None,
    ) -> None:
        self.rate = rate
        self.backlog_bytes = backlog_bytes
        self.projected_wait_s = projected_wait_s
        self.max_wait_s = max_wait_s
        self.upload_class = upload_class
        self.waited_s = waited_s
        super().__init__(failure_message(self))


class UploadAdmissionRejected(UploadAdmissionTimeout):
    pass


def _kb_per_s(rate: float) -> str:
    kb = rate / 1000
    if kb == int(kb):
        return str(int(kb))
    return f"{kb:.1f}"


def failure_message(exc: UploadAdmissionTimeout) -> str:
    if isinstance(exc, UploadAdmissionRejected) or exc.waited_s is None:
        kind = "projected wait"
        seconds = exc.projected_wait_s
    else:
        kind = "waited"
        seconds = exc.waited_s
    return (
        f"upload throttle on at {_kb_per_s(exc.rate)} KB/s; "
        f"{exc.backlog_bytes / 1_000_000:.1f} MB queued ahead; "
        f"{kind} {round(seconds)} s > limit {round(exc.max_wait_s)} s; retry shortly"
    )


def _session_digest(session_key: str | None) -> str:
    return sha256((session_key or "anonymous").encode()).hexdigest()[:12]


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

    def _backlog_bytes(self, upload_class: str, *, behind: _Waiter | None = None) -> int:
        if upload_class == "high":
            outstanding = self._outstanding["high"]
            classes = {"high"}
        else:
            outstanding = self._outstanding["high"] + self._outstanding["batch"]
            classes = {"high", "batch"}
        queued = 0
        for key, flow in self._flows.items():
            if key[0] not in classes:
                continue
            for waiter in flow.queue:
                if waiter is behind:
                    break
                queued += waiter.nbytes
        return outstanding + queued

    def _admits_without_wait(self, upload_class: str) -> bool:
        if self._outstanding[upload_class] != 0:
            return False
        return all(not flow.queue for key, flow in self._flows.items() if key[0] == upload_class)

    def _projection(
        self,
        upload_class: str,
        nbytes: int,
        rate: float,
        *,
        behind: _Waiter | None = None,
    ) -> tuple[int, float]:
        backlog = self._backlog_bytes(upload_class, behind=behind)
        budget = max(rate * OUTSTANDING_SECONDS, 1)
        if rate <= 0:
            return backlog, float("inf")
        cap = HIGH_SHARE * budget if upload_class == "high" else budget
        if upload_class != "high":
            high = self._outstanding["high"]
            backlog -= high - min(high, HIGH_SHARE * budget)
            backlog = max(0, int(backlog))
        need = min(float(nbytes), cap)
        return backlog, max(0.0, backlog + need - cap) / rate

    def _timeout(
        self,
        waiter: _Waiter,
        upload_class: str,
        started: float,
        max_wait: float,
        session_key: str | None,
    ) -> UploadAdmissionTimeout:
        _, rate = upload_throttle.read_state()
        waited = time.monotonic() - started
        backlog, projected = self._projection(upload_class, waiter.nbytes, rate, behind=waiter)
        timed_out = UploadAdmissionTimeout(
            rate=rate,
            backlog_bytes=backlog,
            projected_wait_s=projected,
            max_wait_s=max_wait,
            upload_class=upload_class,
            waited_s=waited,
        )
        self._warn(timed_out, session_key)
        return timed_out

    def _warn(self, exc: UploadAdmissionTimeout, session_key: str | None) -> None:
        seconds = exc.waited_s if exc.waited_s is not None else exc.projected_wait_s
        logger.warning(
            "upload admission class=%s rate_kb_s=%s backlog_mb=%.1f seconds=%.0f limit_s=%.0f session=%s %s",
            exc.upload_class,
            _kb_per_s(exc.rate),
            exc.backlog_bytes / 1_000_000,
            seconds,
            exc.max_wait_s,
            _session_digest(session_key),
            failure_message(exc),
        )

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
        enabled, rate = upload_throttle.read_state()
        if not enabled or os.environ.get("AGENT_LB_UPLOAD_FAIR", "1") == "0":
            yield
            return
        max_wait = float(os.environ.get("AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS", "30"))
        started = time.monotonic()
        cls = _normalize_class(upload_class)
        nbytes = max(nbytes, 0)
        if not self._admits_without_wait(cls):
            backlog, projected = self._projection(cls, nbytes, rate)
            if projected > max_wait:
                rejected = UploadAdmissionRejected(
                    rate=rate,
                    backlog_bytes=backlog,
                    projected_wait_s=projected,
                    max_wait_s=max_wait,
                    upload_class=cls,
                )
                self._warn(rejected, session_key)
                raise rejected
        key = (cls, session_key if session_key is not None else object())
        waiter = _Waiter(nbytes, started + max_wait, cls)
        flow = self._flows.setdefault(key, _Flow())
        flow.queue.append(waiter)
        try:
            while not waiter.admitted:
                self._drain()
                if waiter.admitted:
                    break
                remaining = max_wait - (time.monotonic() - started)
                if remaining <= 0:
                    timed_out = self._timeout(waiter, cls, started, max_wait, session_key)
                    raise timed_out
                try:
                    await asyncio.wait_for(waiter.event.wait(), timeout=min(1.0, remaining))
                except asyncio.TimeoutError:
                    pass
            waited = time.monotonic() - started
            if waited > 5:
                logger.info(
                    "Upload admission session=%s class=%s bytes=%s wait_seconds=%.3f",
                    _session_digest(session_key),
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
