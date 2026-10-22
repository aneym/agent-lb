from __future__ import annotations

import gzip
import http.server
import json
import threading

INPUT_TOKENS = 1_000
CACHE_READ_TOKENS = 4_000
OUTPUT_TOKENS = 500
CAP_MICRODOLLARS = 30_000


def sse_body(model: str) -> bytes:
    start = {
        "type": "message_start",
        "message": {
            "id": "msg_sandbox",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "usage": {
                "input_tokens": INPUT_TOKENS,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": CACHE_READ_TOKENS,
            },
        },
    }
    delta = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": OUTPUT_TOKENS}}
    return (
        f"event: message_start\ndata: {json.dumps(start)}\n\n"
        f"event: message_delta\ndata: {json.dumps(delta)}\n\n"
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    ).encode()


class FakeAnthropic(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int) -> None:
        super().__init__(("127.0.0.1", port), _FakeHandler)
        self.calls: list[str] = []
        self.lock = threading.Lock()


class _FakeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        try:
            model = json.loads(raw or b"{}").get("model", "")
        except ValueError:
            model = ""
        if self.path == "/backend-api/files" or self.path == "/backend-api/files/sandbox-file/uploaded":
            server: FakeAnthropic = self.server  # type: ignore[assignment]
            with server.lock:
                server.calls.append(self.path)
            body = json.dumps({"file_id": "sandbox-file", "status": "success"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.path.rstrip("/").endswith("/v1/messages"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        server: FakeAnthropic = self.server  # type: ignore[assignment]
        with server.lock:
            server.calls.append(model)
        body = sse_body(model)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
