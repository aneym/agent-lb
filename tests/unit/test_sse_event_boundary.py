"""The streamed Anthropic body is forwarded one whole SSE event at a time.

`_sse_event_boundary` decides how much of the held-back buffer is complete events. The SSE
grammar allows CRLF, LF or CR line endings, mixed freely; a CRLF split across reads must not
be mistaken for a blank line. Final-diff review of a03ed2d0 found CR-only events withheld.
"""

from __future__ import annotations

import re
import time

import pytest

from app.modules.proxy.anthropic_service import _sse_event_boundary

FRAMINGS = [
    (b"", b""),
    (b"data: x", b""),
    (b"data: x\n", b""),
    (b"data: x\n\n", b"data: x\n\n"),
    (b"data: x\n\ndata: y", b"data: x\n\n"),
    (b"a\n\nb\n\nc", b"a\n\nb\n\n"),
    (b"data: x\r\n\r\n", b"data: x\r\n\r\n"),
    (b"a\r\nb\r\n\r\nc", b"a\r\nb\r\n\r\n"),
    (b"event: ping\rdata: {}\r\r", b"event: ping\rdata: {}\r\r"),
    (b"data: x\n\r\n", b"data: x\n\r\n"),
    (b"data: x\r\n\n", b"data: x\r\n\n"),
    (b"data: x\r\r\n", b"data: x\r\r\n"),
    # One CRLF ends one line, never a line and a blank line.
    (b"data: x\r\ny\r\n", b""),
    (b"data: x\r\n", b""),
    # A CR may be the first half of a CRLF still in flight; it already ends the blank line.
    (b"data: x\r\n\r", b"data: x\r\n\r"),
    (b"data: x\n\r", b"data: x\n\r"),
    # Blank lines pair off; a lone extra terminator may wait for the next read. It ends no event.
    (b"data: x\n\n\n\ndata: y\r\r\r", b"data: x\n\n\n\ndata: y\r\r"),
]


def _releases(chunks: list[bytes]) -> list[bytes]:
    """Feed reads the way the streaming loop does and return each forward it makes."""
    unsent = bytearray()
    scanned = 0
    released: list[bytes] = []
    for chunk in chunks:
        unsent.extend(chunk)
        cut = _sse_event_boundary(unsent, scanned)
        if cut:
            released.append(bytes(unsent[:cut]))
            del unsent[:cut]
        scanned = len(unsent)
    return released


def _events(stream: bytes) -> list[bytes]:
    """Data payloads a WHATWG SSE parser dispatches from ``stream``."""
    events: list[bytes] = []
    data: list[bytes] = []
    for line in re.split(rb"\r\n|\r|\n", stream)[:-1]:
        if not line:
            if data:
                events.append(b"\n".join(data))
            data = []
        elif line.startswith(b"data:"):
            data.append(line[5:].removeprefix(b" "))
    return events


@pytest.mark.parametrize(("buffer", "complete"), FRAMINGS)
def test_only_complete_events_are_released(buffer: bytes, complete: bytes) -> None:
    assert buffer[: _sse_event_boundary(bytearray(buffer))] == complete


@pytest.mark.parametrize(("buffer", "complete"), FRAMINGS)
def test_any_split_into_reads_releases_the_same_events_and_never_half_of_one(buffer: bytes, complete: bytes) -> None:
    splits = [[buffer[:i], buffer[i:]] for i in range(len(buffer) + 1)]
    splits.append([buffer[i : i + 1] for i in range(len(buffer))])
    for chunks in splits:
        forwards = _releases(chunks)
        released = b"".join(forwards)
        assert buffer.startswith(released), chunks
        assert _events(released) == _events(complete), chunks
        for forward in forwards:
            # Each forward on its own ends on a blank line, so no read sends half an event.
            assert _sse_event_boundary(forward) == len(forward), (chunks, forward)


def test_one_huge_event_over_many_reads_is_scanned_once() -> None:
    # Review of f6537a24: rescanning the whole unfinished event on every read
    # took 7.5 s for one 4 MiB data line arriving in 8 KiB reads.
    event = b"data: " + b"x" * (4 * 1024 * 1024) + b"\n\n"
    chunks = [event[i : i + 8192] for i in range(0, len(event), 8192)]
    started = time.monotonic()
    forwards = _releases(chunks)
    assert forwards == [event]
    assert time.monotonic() - started < 1.0
