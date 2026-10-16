from __future__ import annotations

import re
from contextvars import ContextVar, Token

from starlette.types import Scope

_CLIENT_SESSION_HEADERS = (b"session-id", b"session_id", b"x-codex-session-id", b"x-claude-code-session-id")
_MAX_LENGTH = 128

_CLIENT_SESSION_ID: ContextVar[str | None] = ContextVar("client_session_id", default=None)



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
