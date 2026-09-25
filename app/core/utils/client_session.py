from __future__ import annotations

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


def get_client_session_id() -> str | None:
    return _CLIENT_SESSION_ID.get()


def set_client_session_id_from_scope(scope: Scope) -> Token[str | None]:
    return _CLIENT_SESSION_ID.set(_client_session_id(scope))


def reset_client_session_id(token: Token[str | None]) -> None:
    _CLIENT_SESSION_ID.reset(token)


def _client_session_id(scope: Scope) -> str | None:
    headers = dict(scope.get("headers") or ())
    for name in _CLIENT_SESSION_HEADERS:
        raw = headers.get(name)
        if raw:
            value = raw.decode("latin-1").strip()[:_MAX_LENGTH]
            if value:
                return value
    return None
