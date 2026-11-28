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


class ChunkIdleTimeoutError(TimeoutError):
    def __init__(self, *, seen_bytes: bool) -> None:
        super().__init__("stream_idle" if seen_bytes else "first_byte")
        self.seen_bytes = seen_bytes


async def iter_idle_bounded_chunks(
    chunks: AsyncIterator[bytes],
    *,
    first_byte_deadline: float | None = None,
    idle_seconds: float,
) -> AsyncGenerator[bytes, None]:
    iterator = chunks.__aiter__()
    if first_byte_deadline is None and idle_seconds <= 0:
        async for chunk in iterator:
            yield chunk
        return
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    seen_bytes = False
    waiting = False
    timed_out = False
    wait_started = loop.time()
    handle: asyncio.TimerHandle | None = None

    def _on_timeout() -> None:
        nonlocal handle, timed_out
        if not waiting:
            handle = None
            return
        target = (
            first_byte_deadline
            if not seen_bytes and first_byte_deadline is not None
            else (wait_started + idle_seconds if idle_seconds > 0 else None)
        )
        if target is None:
            handle = None
            return
        now = loop.time()
        if now + 1e-6 < target:
            handle = loop.call_at(target, _on_timeout)
            return
        timed_out = True
        handle = None
        if task is not None:
            task.cancel()

    try:
        while True:
            if seen_bytes:
                if idle_seconds > 0:
                    wait_started = loop.time()
                    if handle is None:
                        handle = loop.call_at(wait_started + idle_seconds, _on_timeout)
            else:
                if first_byte_deadline is not None:
                    if handle is None:
                        handle = loop.call_at(first_byte_deadline, _on_timeout)
                elif idle_seconds > 0:
                    wait_started = loop.time()
                    if handle is None:
                        handle = loop.call_at(wait_started + idle_seconds, _on_timeout)
            waiting = True
            try:
                chunk = await anext(iterator)
            except StopAsyncIteration:
                return
            except asyncio.CancelledError:
                if timed_out:
                    if task is not None:
                        task.uncancel()
                    raise ChunkIdleTimeoutError(seen_bytes=seen_bytes) from None
                raise
            finally:
                waiting = False
            if chunk and not seen_bytes:
                seen_bytes = True
                if first_byte_deadline is not None:
                    if idle_seconds <= 0:
                        if handle is not None:
                            handle.cancel()
                            handle = None
                    else:
                        now = loop.time()
                        if now + idle_seconds < first_byte_deadline and handle is not None:
                            handle.cancel()
                            handle = loop.call_at(now + idle_seconds, _on_timeout)
            yield chunk
    finally:
        if handle is not None:
            handle.cancel()


def find_sse_separator(buffer: bytes | bytearray) -> tuple[int, int] | None:
    lf = buffer.find(b"\n\n")
    crlf = buffer.find(b"\r\n\r\n")
    if lf < 0:
        return (crlf, 4) if crlf >= 0 else None
    if crlf < 0 or lf < crlf:
        return (lf, 2)
    return (crlf, 4)


def pop_sse_event(buffer: bytearray) -> bytes | None:
    separator = find_sse_separator(buffer)
    if separator is None:
        return None
    event_end = separator[0] + separator[1]
    event = bytes(buffer[:event_end])
    del buffer[:event_end]
    return event


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

    loop = asyncio.get_running_loop()
    iterator = source.__aiter__()
    pending: asyncio.Task[str] | None = None
    timer: asyncio.TimerHandle | None = None
    waiter: asyncio.Future[bool] | None = None

    def _on_timer() -> None:
        nonlocal timer
        timer = None
        if waiter is not None and not waiter.done():
            waiter.set_result(False)

    def _on_pending_done(_task: asyncio.Task[str]) -> None:
        if waiter is not None and not waiter.done():
            waiter.set_result(True)

    try:
        deadline = loop.time() + interval_seconds
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            if pending.done():
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    pending = None
                    break
                pending = None
                deadline = loop.time() + interval_seconds
                yield chunk
                continue
            now = loop.time()
            if deadline - now <= 0:
                deadline = now + interval_seconds
                yield keepalive_frame
                continue
            if timer is None:
                timer = loop.call_at(deadline, _on_timer)
            waiter = loop.create_future()
            pending.add_done_callback(_on_pending_done)
            try:
                ready = await waiter
            finally:
                pending.remove_done_callback(_on_pending_done)
                waiter = None
            if not ready:
                now = loop.time()
                if now < deadline:
                    continue
                deadline = now + interval_seconds
                yield keepalive_frame
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                pending = None
                break
            pending = None
            deadline = loop.time() + interval_seconds
            yield chunk
    finally:
        if timer is not None:
            timer.cancel()
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
    if "\r" not in event_block:
        first = event_block.find("data:")
        if first != -1 and (first == 0 or (event_block[first - 1] == "\n" and "data" not in event_block[:first])):
            end = event_block.find("\n", first)
            if end == -1:
                end = len(event_block)
            if "data" not in event_block[end:]:
                start = first + 6 if first + 5 < end and event_block[first + 5] == " " else first + 5
                data = event_block[start:end]
                stripped = data.strip()
                if not stripped or stripped == "[DONE]":
                    return None
                return data
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
