from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping

import anyio

from app.core.clients.failure_observation import observe_error
from app.core.errors import ResponseFailedEvent
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_dict

type JsonPayload = Mapping[str, JsonValue] | ResponseFailedEvent

SSE_KEEPALIVE_FRAME = ": keepalive\n\n"
CODEX_KEEPALIVE_FRAME = 'event: codex.keepalive\ndata: {"type":"codex.keepalive"}\n\n'


async def inject_sse_keepalives(
    source: AsyncIterator[str],
    interval_seconds: float,
    *,
    keepalive_frame: str = SSE_KEEPALIVE_FRAME,
) -> AsyncGenerator[str, None]:

    if interval_seconds <= 0:
        async for chunk in source:
            yield chunk
        return

    iterator = source.__aiter__()
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait((pending,), timeout=interval_seconds)
            if not done:
                yield keepalive_frame
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                pending = None
                break
            pending = None
            yield chunk
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with anyio.CancelScope(shield=True):
                try:
                    await pending
                except asyncio.CancelledError:
                    pass


def format_sse_event(payload: JsonPayload) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type:
        return f"event: {event_type}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


def format_sse_data(payload: Mapping[str, JsonValue]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"data: {data}\n\n"


def parse_sse_data_json(event_block: str) -> dict[str, JsonValue] | None:
    data = extract_sse_data(event_block)
    if data is None:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if is_json_dict(payload):
        observe_error(payload)
        return payload
    return None


def extract_sse_data(event_block: str) -> str | None:
    if "data" not in event_block:
        return None
    single: str | None = None
    extra: list[str] | None = None
    for raw_line in event_block.splitlines():
        if not raw_line or raw_line[0] == ":":
            continue
        if raw_line.startswith("data:"):
            value = raw_line[6:] if len(raw_line) > 5 and raw_line[5] == " " else raw_line[5:]
        elif raw_line == "data":
            value = ""
        else:
            continue
        if single is None:
            single = value
        elif extra is None:
            extra = [single, value]
        else:
            extra.append(value)
    if single is None:
        return None
    data = "\n".join(extra) if extra is not None else single
    stripped = data.strip()
    if not stripped or stripped == "[DONE]":
        return None
    return data
