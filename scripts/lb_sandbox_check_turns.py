from __future__ import annotations

import http.client
import json
import time

MAX_TURN_BYTES = 256 << 10


MAX_TURN_TEXT = 200


TURN_DEADLINE = 120.0


class TurnBudgetExceeded(Exception):
    pass


def post(port: int, path: str, body: dict, headers: dict, text_of=None) -> tuple[int, bytes]:

    deadline = time.monotonic() + TURN_DEADLINE
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("POST", path, body=json.dumps(body), headers={"content-type": "application/json", **headers})
        resp = conn.getresponse()
        raw = b""
        while True:
            if time.monotonic() > deadline:
                raise TurnBudgetExceeded("deadline")
            chunk = resp.read1(16384)
            if not chunk:
                return resp.status, raw
            raw += chunk
            if len(raw) > MAX_TURN_BYTES:
                raise TurnBudgetExceeded("bytes")
            if text_of is not None and len(text_of(raw)) > MAX_TURN_TEXT:
                raise TurnBudgetExceeded("output text")
    finally:
        conn.close()


def sse_events(raw: bytes):
    for line in raw.decode(errors="replace").splitlines():
        if line.startswith("data:"):
            try:
                event = json.loads(line[5:])
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event


def anthropic_text(raw: bytes) -> str:
    return "".join(
        e.get("delta", {}).get("text", "") for e in sse_events(raw) if e.get("type") == "content_block_delta"
    )


def codex_text(raw: bytes) -> str:
    return "".join(e.get("delta", "") for e in sse_events(raw) if e.get("type") == "response.output_text.delta")


def anthropic_turn(port: int, model: str, stream: bool = False) -> dict:
    body = {
        "model": model,
        "max_tokens": 48 if stream else 16,
        "messages": [
            {"role": "user", "content": "Count from 1 to 15, comma separated." if stream else "Reply with exactly: OK"}
        ],
    }
    if stream:
        body["stream"] = True
    started = time.monotonic()
    try:
        status, raw = post(
            port, "/v1/messages", body, {"anthropic-version": "2023-06-01"}, anthropic_text if stream else None
        )
    except TurnBudgetExceeded as exc:
        return {"status": 0, "error": f"budget: {exc}", "text": "", "complete": False}
    except (OSError, http.client.HTTPException) as exc:
        return {"status": 0, "error": exc.__class__.__name__, "text": "", "complete": False}
    text = ""
    if stream:
        text = anthropic_text(raw)
        complete = b"message_stop" in raw
    else:
        try:
            text = "".join(c.get("text", "") for c in json.loads(raw).get("content", []))
        except (ValueError, AttributeError):
            text = ""
        complete = bool(text)
    return {"status": status, "text": text[:200], "complete": complete, "seconds": round(time.monotonic() - started, 2)}


def codex_turn(port: int, model: str) -> dict:
    body = {
        "model": model,
        "instructions": "You are a terse test responder.",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Reply with exactly: OK"}]}
        ],
        "stream": True,
        "store": False,
        "reasoning": {"effort": "low"},
    }
    started = time.monotonic()
    try:
        status, raw = post(port, "/backend-api/codex/responses", body, {}, codex_text)
    except TurnBudgetExceeded as exc:
        return {"status": 0, "error": f"budget: {exc}", "text": "", "complete": False, "output_tokens": None}
    except (OSError, http.client.HTTPException) as exc:
        return {"status": 0, "error": exc.__class__.__name__, "text": "", "complete": False, "output_tokens": None}
    text = codex_text(raw)
    usage = {}
    for event in sse_events(raw):
        if event.get("type") == "response.completed":
            usage = (event.get("response") or {}).get("usage") or {}
    complete = b"response.completed" in raw
    return {
        "status": status,
        "text": text[:200],
        "complete": complete,
        "output_tokens": usage.get("output_tokens"),
        "seconds": round(time.monotonic() - started, 2),
    }


def within_budget(edge: str, turn: dict) -> bool:

    if edge != "openai":
        return True
    tokens = turn.get("output_tokens")
    return isinstance(tokens, int) and not isinstance(tokens, bool) and tokens < 50


def dashboard_export_status(port: int, forged_user: str | None = None) -> int:

    headers = {"content-type": "application/json"}
    if forged_user:
        headers["Remote-User"] = forged_user
        headers["X-Forwarded-For"] = "192.0.2.1"
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        conn.request("POST", "/api/accounts/lbsbx-probe/export/auth", body=b"{}", headers=headers)
        status = conn.getresponse().status
        conn.close()
        return status
    except (OSError, http.client.HTTPException):
        return 0


def in_flight(port: int) -> int:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        conn.request("GET", "/internal/drain/status")
        data = json.loads(conn.getresponse().read())
        conn.close()
        return int(data["checks"]["in_flight"])
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return -1
