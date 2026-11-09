from __future__ import annotations

import json
from typing import Any

TOKEN_FIELDS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
    "cache_creation_input_tokens": "cache_write_tokens",
}


class UsageTally:
    def __init__(self, model: str | None) -> None:
        self.model = model
        self.message_id: str | None = None
        self.tokens = {name: 0 for name in TOKEN_FIELDS.values()}
        self.seen = False
        self._pending = b""

    def _take(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for source, name in TOKEN_FIELDS.items():
            value = usage.get(source)
            if isinstance(value, int) and value >= 0:
                self.tokens[name] = value
                self.seen = True

    def _message(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        if isinstance(message.get("id"), str):
            self.message_id = message["id"]
        if isinstance(message.get("model"), str):
            self.model = message["model"]
        self._take(message.get("usage"))

    def feed_event(self, data: str) -> None:
        try:
            event = json.loads(data)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if event.get("type") == "message_start":
            self._message(event.get("message"))
        elif event.get("type") == "message_delta":
            self._take(event.get("usage"))

    def feed_stream(self, chunk: bytes) -> None:
        self._pending += chunk
        *lines, self._pending = self._pending.split(b"\n")
        for line in lines:
            if line.startswith(b"data:"):
                self.feed_event(line[5:].strip().decode("utf-8", "replace"))

    def feed_body(self, body: bytes) -> None:
        try:
            self._message(json.loads(body))
        except ValueError:
            return

    def report(self, fallback_id: str) -> dict[str, Any] | None:
        if not self.seen or not self.model:
            return None
        return {"report_id": self.message_id or fallback_id, "model": self.model, **self.tokens}
