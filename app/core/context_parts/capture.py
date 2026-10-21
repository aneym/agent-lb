from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

import anyio
from starlette.types import Receive, Scope

from app.core.context_parts.measure import MAX_BODY_BYTES, PartsMeasure, measure

MEASURED_PATHS = frozenset(
    {"/v1/messages", "/v1/ccgpt/messages", "/v1/responses", "/backend-api/codex/responses", "/v1/chat/completions"}
)


@dataclass
class ContextPartsState:
    path: str
    chunks: list[bytes]
    complete: bool = False
    size: int = 0
    measured: PartsMeasure | None = None
    done: bool = False


@dataclass(frozen=True, slots=True)
class Captured:
    parts: PartsMeasure | None
    body_bytes: int


context_parts_state: ContextVar[ContextPartsState | None] = ContextVar("context_parts_state", default=None)


def wrap_receive(scope: Scope, receive: Receive) -> tuple[Receive, Token | None]:
    path = scope["path"].rstrip("/")
    if scope["type"] != "http" or scope["method"] != "POST" or path not in MEASURED_PATHS:
        return receive, None
    state = ContextPartsState(path, [])
    token = context_parts_state.set(state)

    async def captured_receive():
        message = await receive()
        if message["type"] == "http.request" and not state.complete:
            chunk = message.get("body", b"")
            state.size += len(chunk)
            if state.size <= MAX_BODY_BYTES:
                state.chunks.append(chunk)
            else:
                state.chunks.clear()
            state.complete = not message.get("more_body", False)
        return message

    return captured_receive, token


async def measured_parts() -> Captured | None:
    state = context_parts_state.get()
    if state is None:
        return None
    if not state.done and state.complete:
        if state.size <= MAX_BODY_BYTES:
            state.measured = await anyio.to_thread.run_sync(measure, state.path, b"".join(state.chunks))
        state.chunks.clear()
        state.done = True
    return Captured(state.measured, state.size)
