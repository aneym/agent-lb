from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.db.request_models import RequestLog
from app.db.session import SessionLocal


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cursor_pages_tied_timestamps_during_inserts(async_client):
    until = datetime(2026, 1, 2)
    async with SessionLocal() as session:
        session.add_all(
            [
                RequestLog(
                    request_id=f"original-{i}",
                    model="test",
                    status="success",
                    requested_at=until - timedelta(seconds=i // 250),
                )
                for i in range(1000)
            ]
        )
        await session.commit()
    params = {"limit": 200, "includeTotal": "false", "until": until.isoformat()}
    ids = []
    for page_number in range(5):
        response = await async_client.get("/api/request-logs", params=params)
        assert response.status_code == 200
        body = response.json()
        ids.extend(row["requestId"] for row in body["requests"])
        params["cursor"] = body["nextCursor"]
        async with SessionLocal() as session:
            session.add(RequestLog(request_id=f"new-{page_number}", model="test", status="success", requested_at=until))
            session.add(
                RequestLog(
                    request_id=f"future-{page_number}",
                    model="test",
                    status="success",
                    requested_at=until + timedelta(seconds=1),
                )
            )
            await session.commit()
    expected = sorted(range(1000), key=lambda i: (-(i // 250), i), reverse=True)
    assert ids == [f"original-{i}" for i in expected]
    assert len(set(ids)) == 1000
    response = await async_client.get("/api/request-logs", params=params)
    assert response.json()["requests"] == []
    for cursor in ("invalid", "2026-01-02|0", "not-a-date|1"):
        response = await async_client.get("/api/request-logs", params={"cursor": cursor})
        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.parametrize("recover", [True, False])
def test_truncated_503_retries_are_bounded(tmp_path, recover):
    attempts = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            attempts.append(self.path)
            healthy = recover and len(attempts) == 2
            body = b'{"requests": []}' if healthy else b'{"error":'
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Length", str(len(body) if healthy else 1000))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        code = (
            "from pathlib import Path; from route_cache import request; "
            f"r = request('GET', '/api/request-logs', payload=None, timeout=2, "
            f"url='http://127.0.0.1:{server.server_port}', cache_dir=Path({str(tmp_path)!r})); "
            f"assert r.ok is {recover!r}; assert r.status == {200 if recover else 503}; "
            + ("assert r.error.startswith('truncated:')" if not recover else "assert r.body == {'requests': []}")
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[2] / "clients",
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert len(attempts) == (2 if recover else 3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
