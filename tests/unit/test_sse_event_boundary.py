"""The streamed Anthropic body is forwarded one whole SSE event at a time.

`_sse_event_boundary` decides how much of the held-back buffer is complete events. The SSE
grammar allows CRLF, LF or CR line endings, mixed freely; a CRLF split across reads must not
be mistaken for a blank line. Final-diff review of a03ed2d0 found CR-only events withheld.
"""

from __future__ import annotations

import pytest

from app.modules.proxy.anthropic_service import _sse_event_boundary


@pytest.mark.parametrize(
    ("buffer", "complete"),
    [
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
    ],
)
def test_only_complete_events_are_released(buffer: bytes, complete: bytes) -> None:
    assert buffer[: _sse_event_boundary(bytearray(buffer))] == complete
