from __future__ import annotations

import http.client
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        pass

    def do_POST(self):
        assert self.path.endswith("/v1/messages"), self.path
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        hot = self.headers.get("x-check-cache") == "hot"
        usage = {
            "input_tokens": 200 if hot else 1200,
            "output_tokens": 7,
            "cache_creation_input_tokens": 0 if hot else 30000,
            "cache_read_input_tokens": 1000 if hot else 0,
        }
        message = {
            "id": "msg_check",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": usage,
        }
        if body.get("stream"):
            events = [
                ("message_start", {"type": "message_start", "message": {**message, "content": []}}),
                (
                    "content_block_start",
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                ),
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "message_delta",
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}},
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
            payload = "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in events).encode()
        else:
            payload = json.dumps(message).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def request(base, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read()
            return response.status, payload
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def split(prompt):
    weights = [2000, 139, 1111, 3000]
    exact = [divmod(prompt * weight, 6250) for weight in weights]
    allocated = [whole for whole, remainder in exact]
    for index in sorted(range(4), key=lambda i: (-exact[i][1], i))[: prompt - sum(allocated)]:
        allocated[index] += 1
    return dict(zip(("system", "tools", "history", "toolResults"), allocated))


def browser(session):
    command = shutil.which("playwright-cli")
    assert command, "playwright-cli not on PATH"
    probe = subprocess.run([command, "cli", "--help"], capture_output=True, text=True, timeout=60)
    prefix = [command, "cli"] if probe.returncode == 0 and "-s=<session>" in probe.stdout else [command]

    def run(*args):
        done = subprocess.run([*prefix, f"-s={session}", *args], capture_output=True, text=True, timeout=120)
        assert done.returncode == 0 and "### Error" not in done.stdout, (
            f"{args[0]}: {done.stdout[-600:]}{done.stderr[-600:]}"
        )
        return done.stdout

    def evaluate(script):
        lines = run("eval", script).splitlines()
        start = lines.index("### Result") + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("### ")), len(lines))
        return json.loads("\n".join(lines[start:end]))

    def close():
        subprocess.run([*prefix, f"-s={session}", "close"], capture_output=True, timeout=60)

    return run, evaluate, close


BARS = """() => ({
  bars: [...document.querySelectorAll('[data-testid=trace-bar]')].map((e) => ({
    cache: e.dataset.cache,
    band: e.dataset.band,
    tag: e.closest('[data-testid=trace-row]').querySelector('[data-testid=trace-tag]')?.textContent ?? null,
    latency: e.dataset.latency,
    parts: e.dataset.parts,
    width: e.getBoundingClientRect().width,
  })),
  prompt: document.querySelector('[data-testid=trace-totals]')?.dataset.prompt ?? null,
})"""
PANEL = """() => {
  const e = document.querySelector('[data-testid=trace-panel]');
  return e && e.checkVisibility() ? { logId: e.dataset.logId, prompt: e.dataset.prompt } : null;
}"""


def check_page(base, sid, trace):
    connection = http.client.HTTPConnection(base.removeprefix("http://"), timeout=30)
    connection.request("GET", f"/s/{sid[:12]}")
    response = connection.getresponse()
    response.read()
    connection.close()
    assert response.status == 302, f"short link status {response.status}"
    assert response.getheader("Location") == f"/sessions/{sid}", response.getheader("Location")
    rows = trace["requests"]
    run, evaluate, close = browser("trace-check-" + secrets.token_hex(4))
    try:
        run("open", "--browser=chromium", f"{base}/s/{sid}")
        deadline = time.monotonic() + 15
        while True:
            seen = evaluate(BARS)
            if len(seen["bars"]) >= 3 or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        bars = seen["bars"]
        assert len(bars) == 6, seen
        for bar, row in zip(bars, rows):
            assert bar["cache"] == (row["cacheKind"] or "none"), (bar, row["cacheKind"])
            assert bar["width"] > 0, bar
        assert bars[4]["band"] == "high", bars[4]
        for i in (0, 1, 2, 3, 5):
            assert bars[i]["band"] == "low", (i, bars[i])
        assert bars[0]["tag"] == "first write", bars[0]
        for bar, row in zip(bars[:5], rows[:5]):
            parts = row["estParts"]
            assert bar["parts"] == f"{parts['system']},{parts['tools']},{parts['history']},{parts['toolResults']}", bar
        assert bars[5]["parts"] == "", bars[5]
        assert seen["prompt"] == str(trace["totals"]["promptTokens"]), seen["prompt"]
        evaluate("() => { document.querySelector('[data-testid=trace-row]').focus(); return true; }")
        run("press", "Enter")
        panel = evaluate(PANEL)
        assert panel == {"logId": str(rows[0]["logId"]), "prompt": str(rows[0]["promptTokens"])}, panel
        run("press", "Escape")
        assert evaluate(PANEL) is None, "panel stayed open after Escape"
        run("screenshot", f"--filename={Path(tempfile.gettempdir()) / 'session-trace-check.png'}")
    finally:
        close()
    return f" page={len(bars)} bars panel=ok"


def main():
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="trace-check-") as directory:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            environment = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LB_")}
            environment.update(
                {
                    "HOME": directory,
                    "AGENT_LB_DATA_DIR": directory,
                    "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{directory}/store.db",
                    "AGENT_LB_METRICS_ENABLED": "false",
                    "AGENT_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED": "false",
                    "AGENT_LB_GLM_ANTHROPIC_UPSTREAM_BASE_URL": f"http://127.0.0.1:{upstream.server_port}/api/anthropic",
                }
            )
            with sqlite3.connect(Path(directory) / "store.db") as database:
                database.execute("PRAGMA journal_mode=WAL")
            with open(Path(directory) / "server.log", "w+") as log:
                subprocess.run(
                    [sys.executable, "-m", "app.db.migrate", "upgrade", "head"],
                    env=environment,
                    stdout=log,
                    stderr=log,
                    check=True,
                    timeout=300,
                )
                subprocess.run(
                    [sys.executable, "-m", "app.db.migrate", "check"],
                    env=environment,
                    stdout=log,
                    stderr=log,
                    check=True,
                    timeout=30,
                )
                process = subprocess.Popen(
                    [sys.executable, "-m", "app.cli", "--host", "127.0.0.1", "--port", str(port)],
                    env=environment,
                    stdout=log,
                    stderr=log,
                )
                try:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        assert process.poll() is None, "server exited during startup"
                        try:
                            if request(base, "/health/ready")[0] == 200:
                                break
                        except OSError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise AssertionError("readiness timeout")
                    status, _ = request(
                        base, "/api/accounts/import/api-key", {"provider": "glm", "apiKey": "check-not-a-real-key"}
                    )
                    assert status == 200, f"import status {status}"
                    sid = "trace-check-" + secrets.token_hex(8)
                    headers = {"x-claude-code-session-id": sid}
                    tools = [{"name": name, "input_schema": {"type": "object"}} for name in ("a", "b", "c")]
                    body: dict[str, Any] = {
                        "model": "glm-4.7",
                        "max_tokens": 16,
                        "system": "s" * 2000,
                        "tools": tools,
                        "messages": [
                            {"role": "user", "content": "h" * 1000},
                            {"role": "assistant", "content": "a" * 100},
                            {
                                "role": "user",
                                "content": [{"type": "tool_result", "tool_use_id": "x", "content": "r" * 3000}],
                            },
                            {"role": "assistant", "content": "b" * 10},
                            {"role": "user", "content": "c"},
                        ],
                    }
                    assert len(json.dumps(tools, separators=(",", ":"))) == 139
                    print("fixture tools_chars=139 history_chars=1111")
                    for index in range(6):
                        payload = dict(body)
                        if index == 3:
                            payload["stream"] = True
                        if index == 4:
                            headers["x-check-cache"] = "hot"
                        else:
                            headers.pop("x-check-cache", None)
                        if index == 5:
                            payload["padding"] = "p" * 4194304
                        status, response = request(base, "/v1/messages", payload, headers)
                        if status == 401:
                            key_status, key_data = request(base, "/api/api-keys/", {"name": "trace-check"})
                            assert key_status == 200
                            headers["x-api-key"] = json.loads(key_data)["key"]
                            status, response = request(base, "/v1/messages", payload, headers)
                        assert status == 200, f"proxy request {index} status {status}: {response[:300]!r}"
                        if index == 3:
                            assert b"message_stop" in response
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        status, data = request(base, f"/api/sessions/{sid}/trace")
                        trace = json.loads(data)
                        if status == 200 and trace["totals"]["requests"] == 6:
                            break
                        time.sleep(0.1)
                    assert status == 200, f"trace status {status}"
                    rows = trace["requests"]
                    assert len(rows) == 6, trace["totals"]
                    for row in rows[:5]:
                        assert row["bodySchema"] == "anthropic_messages", row
                        assert row["partChars"] == {
                            "system": 2000,
                            "tools": 139,
                            "history": 1111,
                            "toolResults": 3000,
                        }, row
                        assert row["estParts"] == split(row["promptTokens"]), row
                    assert rows[0]["cacheKind"] == "first_write", rows[0]
                    assert rows[4]["cacheKind"] is None and rows[4]["cacheReadTokens"] == 1000, rows[4]
                    assert rows[5]["partChars"] is None and rows[5]["estParts"] is None, rows[5]
                    assert rows[5]["bodyBytes"] > 4194304, rows[5]
                    assert trace["totals"]["measured"] == 5 and trace["totals"]["coverage"] == 0.8333, trace["totals"]
                    page = check_page(base, sid, trace) if "--page" in sys.argv[1:] else ""
                    print(
                        "session_trace ok requests=6 measured=5 coverage=0.8333 stream=measured hot=None first=first_write"
                        + page
                    )
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    finally:
        upstream.shutdown()
        upstream.server_close()


if __name__ == "__main__":
    main()
