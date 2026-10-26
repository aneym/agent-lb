from __future__ import annotations

import re
from collections import OrderedDict
from contextvars import ContextVar, Token
from dataclasses import dataclass

from starlette.types import Scope

_CLIENT_SESSION_HEADERS = (b"session-id", b"session_id", b"x-codex-session-id", b"x-claude-code-session-id")
_MAX_LENGTH = 128

_CLIENT_SESSION_ID: ContextVar[str | None] = ContextVar("client_session_id", default=None)

_FACTORY_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class FactoryAttribution:
    project: str | None = None
    owner: str | None = None
    host: str | None = None
    job: str | None = None


FACTORY_ATTRIBUTION: ContextVar[FactoryAttribution] = ContextVar("factory_attribution", default=FactoryAttribution())


def factory_attribution_from_scope(scope: Scope) -> FactoryAttribution:
    headers = dict(scope.get("headers") or ())
    project = headers.get(b"x-factory-project", b"").decode("latin-1").strip()
    job = headers.get(b"x-factory-job", b"").decode("latin-1").strip()
    return validated_factory_attribution(project, job)


def validated_factory_attribution(project: str, job: str) -> FactoryAttribution:
    if not (_FACTORY_VALUE.fullmatch(project) and _FACTORY_VALUE.fullmatch(job)):
        return FactoryAttribution()
    from app.core.config.settings import get_settings

    settings = get_settings()
    return FactoryAttribution(
        project=project,
        owner=settings.identity_owner,
        host=settings.identity_local_machine or settings.local_instance_id,
        job=job,
    )


_SEAT_HEADER = b"x-agent-lb-seat"
_SEAT_RE = re.compile(r"[a-z0-9][a-z0-9@._:-]{0,63}\Z")
_DISPATCH_ID: ContextVar[str | None] = ContextVar("dispatch_id", default=None)
_DISPATCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:-]{0,59}\Z")
_CALLER_SEAT: ContextVar[str | None] = ContextVar("caller_seat", default=None)


def get_client_session_id() -> str | None:
    return _CLIENT_SESSION_ID.get()


def set_client_session_id_from_scope(scope: Scope) -> Token[str | None]:
    return _CLIENT_SESSION_ID.set(_client_session_id(scope))


def reset_client_session_id(token: Token[str | None]) -> None:
    _CLIENT_SESSION_ID.reset(token)


def get_dispatch_id() -> str | None:
    return _DISPATCH_ID.get()


def set_dispatch_id_from_scope(scope: Scope) -> Token[str | None]:
    headers = dict(scope.get("headers") or ())
    raw = headers.get(b"x-agent-lb-dispatch")
    value = raw.decode("latin-1").strip() if raw else ""
    return _DISPATCH_ID.set(value if _DISPATCH_RE.fullmatch(value) else None)


def reset_dispatch_id(token: Token[str | None]) -> None:
    _DISPATCH_ID.reset(token)


def get_caller_seat() -> str | None:
    return _CALLER_SEAT.get()


def set_caller_seat_from_scope(scope: Scope) -> Token[str | None]:
    return _CALLER_SEAT.set(_caller_seat(scope))


def set_caller_seat(value: str) -> Token[str | None]:
    seat = value.strip().lower()
    return _CALLER_SEAT.set(seat if _SEAT_RE.fullmatch(seat) else None)


def reset_caller_seat(token: Token[str | None]) -> None:
    _CALLER_SEAT.reset(token)


def _client_session_id(scope: Scope) -> str | None:
    headers = dict(scope.get("headers") or ())
    for name in _CLIENT_SESSION_HEADERS:
        raw = headers.get(name)
        if raw:
            value = raw.decode("latin-1").strip()[:_MAX_LENGTH]
            if value:
                return value
    return None


def _caller_seat(scope: Scope) -> str | None:
    headers = dict(scope.get("headers") or ())
    raw = headers.get(_SEAT_HEADER)
    if not raw:
        dispatch_id = get_dispatch_id()
        return f"dcn:{dispatch_id}" if dispatch_id else None
    value = raw.decode("latin-1").strip().lower()
    if _SEAT_RE.fullmatch(value):
        return value
    return None


_ROOM_HEADER = b"x-agent-lb-room"
_ROOM_RE = re.compile(r"[a-z0-9][a-z0-9@._:/-]{0,127}\Z")
_ROOM: ContextVar[str | None] = ContextVar("room", default=None)


def get_room() -> str | None:
    return _ROOM.get()


def set_room_from_scope(scope: Scope) -> Token[str | None]:
    headers = dict(scope.get("headers") or ())
    raw = headers.get(_ROOM_HEADER)
    value = raw.decode("latin-1").strip().lower() if raw else ""
    return _ROOM.set(value if _ROOM_RE.fullmatch(value) else None)


def reset_room(token: Token[str | None]) -> None:
    _ROOM.reset(token)


_LANE_HEADER = b"x-agent-lb-lane"
_LANE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:/-]{0,127}\Z")
_JOB_HEADER = b"x-agent-lb-job"
_JOB_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:/-]{0,159}\Z")
_JOB_PATH = re.compile(rb"/jobs/(seat-[A-Za-z0-9][A-Za-z0-9._-]{0,158})")
_BODY_SCAN_LIMIT = 2_000_000
_LANE: ContextVar[str | None] = ContextVar("lane", default=None)
_JOB: ContextVar[str | None] = ContextVar("job", default=None)
_SESSION_ATTRIBUTION: OrderedDict[str, tuple[str | None, str | None]] = OrderedDict()
_SESSION_ATTRIBUTION_LIMIT = 4096


def get_lane() -> str | None:
    return _LANE.get()


def get_job_id() -> str | None:
    return _JOB.get()


class JobBodyScan:
    def __init__(self) -> None:
        self.tail = b""
        self.counts: dict[str, int] = {}
        self.seen = 0

    def feed(self, data: bytes) -> str | None:
        if not data or self.seen >= _BODY_SCAN_LIMIT:
            return self.best()
        data = data[: _BODY_SCAN_LIMIT - self.seen]
        self.seen += len(data)
        blob = self.tail + data
        for match in _JOB_PATH.finditer(blob):
            job = match.group(1).decode("ascii", "ignore")
            if _JOB_RE.fullmatch(job):
                self.counts[job] = self.counts.get(job, 0) + 1
        self.tail = blob[-180:]
        return self.best()

    def best(self) -> str | None:
        if not self.counts:
            return None
        return max(self.counts, key=lambda name: (self.counts[name], len(name)))


def remember_job(job: str | None) -> None:
    if not job or not _JOB_RE.fullmatch(job):
        return
    current = _JOB.get()
    if current is None or (job.startswith("seat-") and not str(current).startswith("seat-")):
        _JOB.set(job)
    session = get_client_session_id()
    if not session:
        return
    lane, previous = _SESSION_ATTRIBUTION.pop(session, (None, None))
    _SESSION_ATTRIBUTION[session] = (lane, _JOB.get() or previous or job)
    while len(_SESSION_ATTRIBUTION) > _SESSION_ATTRIBUTION_LIMIT:
        _SESSION_ATTRIBUTION.popitem(last=False)


def set_attribution_from_scope(scope: Scope) -> tuple[Token[str | None], Token[str | None]]:
    headers = dict(scope.get("headers") or ())
    lane = _header_value(headers, _LANE_HEADER, _LANE_RE)
    job = _header_value(headers, _JOB_HEADER, _JOB_RE)
    session = get_client_session_id()
    if session:
        if lane or job:
            remembered = _SESSION_ATTRIBUTION.pop(session, (None, None))
            _SESSION_ATTRIBUTION[session] = (lane or remembered[0], job or remembered[1])
            while len(_SESSION_ATTRIBUTION) > _SESSION_ATTRIBUTION_LIMIT:
                _SESSION_ATTRIBUTION.popitem(last=False)
        else:
            lane, job = _SESSION_ATTRIBUTION.get(session, (None, None))
    return _LANE.set(lane), _JOB.set(job)


def reset_attribution(tokens: tuple[Token[str | None], Token[str | None]]) -> None:
    _LANE.reset(tokens[0])
    _JOB.reset(tokens[1])


def _header_value(headers: dict[bytes, bytes], name: bytes, pattern: re.Pattern[str]) -> str | None:
    raw = headers.get(name)
    value = raw.decode("latin-1").strip() if raw else ""
    return value if pattern.fullmatch(value) else None
