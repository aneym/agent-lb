from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / "scripts" / "t3-thread-collector"
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def statev2(tmp_path: Path) -> Path:
    path = tmp_path / "statev2.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript((FIXTURES / "t3_statev2.sql").read_text())
    connection.commit()
    connection.close()
    return path


def collect(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(COLLECTOR), *args], capture_output=True, text=True, timeout=60, check=False
    )


class Sink:
    def __init__(self) -> None:
        self.bodies: list[dict] = []
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers["Content-Length"])
                sink.bodies.append({"path": self.path, **json.loads(self.rfile.read(size))})
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sink():
    receiver = Sink()
    yield receiver
    receiver.close()


def test_dry_run_matches_the_recorded_registration(statev2: Path, tmp_path: Path) -> None:
    done = collect("--host", "studio", "--db", str(statev2), "--state", str(tmp_path / "s.json"), "--dry-run")
    assert done.returncode == 0, done.stderr
    expected = json.loads((FIXTURES / "t3_collector_dry_run.json").read_text())
    assert json.loads(done.stdout) == expected
    assert not (tmp_path / "s.json").exists()


def test_post_failure_keeps_the_cursor_and_a_later_run_sends_only_what_changed(
    statev2: Path, tmp_path: Path, sink: Sink
) -> None:
    state = tmp_path / "s.json"
    common = ["--host", "studio", "--db", str(statev2), "--state", str(state)]
    connection = sqlite3.connect(statev2)
    connection.execute(
        "UPDATE orchestration_v2_projection_threads SET updated_at = '2026-10-26T08:00:00.000Z'"
        " WHERE thread_id != 'mcp:eeee'"
    )
    connection.execute(
        "UPDATE orchestration_v2_projection_provider_threads SET updated_at = '2026-10-26T08:00:00.000Z'"
    )
    connection.commit()
    connection.close()
    failed = collect(*common, "--base-url", "http://127.0.0.1:9")
    assert failed.returncode == 1
    assert not state.exists()

    sent = collect(*common, "--base-url", sink.url)
    assert sent.returncode == 0, sent.stderr
    body = sink.bodies[0]
    assert body["path"] == "/api/t3/threads/sync" and body["host"] == "studio"
    assert {item["threadId"] for item in body["threads"]} >= {"mcp:aaaa", "mcp:dddd", "mcp:eeee"}
    assert {item["nativeId"] for item in body["nativeSessions"]} == {
        "00000000-0000-4000-8000-00000000000a",
        "01a00000-0000-7000-8000-00000000000b",
        "00000000-0000-4000-8000-00000000000d",
    }

    connection = sqlite3.connect(statev2)
    connection.execute(
        "UPDATE orchestration_v2_projection_threads SET title = 'Renamed', updated_at = '2026-10-26T18:00:00.000Z'"
        " WHERE thread_id = 'mcp:eeee'"
    )
    connection.commit()
    connection.close()
    sink.bodies.clear()
    again = collect(*common, "--base-url", sink.url)
    assert again.returncode == 0, again.stderr
    assert [(item["threadId"], item["title"]) for item in sink.bodies[0]["threads"]] == [("mcp:eeee", "Renamed")]
    assert sink.bodies[0]["nativeSessions"] == []


def test_schema_drift_fails_loudly_and_posts_nothing(statev2: Path, tmp_path: Path, sink: Sink) -> None:
    connection = sqlite3.connect(statev2)
    connection.execute("ALTER TABLE orchestration_v2_projection_provider_threads DROP COLUMN driver")
    connection.commit()
    connection.close()
    done = collect(
        "--host", "studio", "--db", str(statev2), "--state", str(tmp_path / "s.json"), "--base-url", sink.url
    )
    assert done.returncode == 3
    assert "driver" in done.stderr
    assert sink.bodies == []
