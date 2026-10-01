from __future__ import annotations

import re
from contextvars import ContextVar, Token

from starlette.types import Scope

# The client's own conversation id, as the client names it on the wire. Codex 0.157
# sends `session-id` (= its rollout file id); Claude Code sends
# `x-claude-code-session-id`. Request logs store it beside agent-lb's own
# session_id (for Codex websockets a minted `turn_<hex>`), so a harness can join
# a trial's transcript to its requests even when trials run concurrently.
_CLIENT_SESSION_HEADERS = (b"session-id", b"session_id", b"x-codex-session-id", b"x-claude-code-session-id")
_MAX_LENGTH = 128

_CLIENT_SESSION_ID: ContextVar[str | None] = ContextVar("client_session_id", default=None)

# Launcher seat from AGENT_LB_SEAT, forwarded as x-agent-lb-seat. Request logs
# store it so the token audit can name the seat when no transcript does.
_SEAT_HEADER = b"x-agent-lb-seat"
_SEAT_RE = re.compile(r"[a-z0-9][a-z0-9@._:-]{0,63}\Z")
_CALLER_SEAT: ContextVar[str | None] = ContextVar("caller_seat", default=None)


def get_client_session_id() -> str | None:
    return _CLIENT_SESSION_ID.get()


def set_client_session_id_from_scope(scope: Scope) -> Token[str | None]:
    return _CLIENT_SESSION_ID.set(_client_session_id(scope))


def reset_client_session_id(token: Token[str | None]) -> None:
    _CLIENT_SESSION_ID.reset(token)


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
        return None
    value = raw.decode("latin-1").strip().lower()
    if _SEAT_RE.fullmatch(value):
        return value
    return None
