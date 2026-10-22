from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class CodexControlUsage:
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None

    def observe(self, body: bytes | None, *, request: bool = False) -> None:
        if not body:
            return
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            for block in body.replace(b"\r\n", b"\n").split(b"\n\n"):
                data = b"\n".join(line[5:].lstrip(b" ") for line in block.splitlines() if line.startswith(b"data:"))
                if data and data != b"[DONE]":
                    try:
                        self.observe_payload(json.loads(data))
                    except (ValueError, UnicodeDecodeError):
                        continue
        else:
            self.observe_payload(payload, request=request)

    def observe_payload(self, payload: object, *, request: bool = False) -> None:
        if not isinstance(payload, dict):
            return
        event_type = payload.get("type")
        response = payload.get("response")
        if event_type in ("response.created", "response.completed", "response.failed", "response.incomplete"):
            if not isinstance(response, dict):
                return
            payload = response
        model = payload.get("model")
        if isinstance(model, str) and model.strip():
            self.model = model
        if request or event_type == "response.created":
            return
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        for name in ("input_tokens", "output_tokens"):
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                setattr(self, name, value)
        for details_name, token_name, target in (
            ("input_tokens_details", "cached_tokens", "cached_input_tokens"),
            ("output_tokens_details", "reasoning_tokens", "reasoning_tokens"),
        ):
            details = usage.get(details_name)
            value = details.get(token_name) if isinstance(details, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                setattr(self, target, value)
