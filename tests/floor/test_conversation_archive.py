from __future__ import annotations

import queue
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from app.core import conversation_archive
from app.core.utils.request_id import reset_request_id, set_request_id
from tests.floor.conversation_archive_support import (
    _archive_lines,
    _archive_records,
    _ArchiveSettings,
)


def test_archive_json_writes_redacted_jsonl_record(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    token = set_request_id("req_archive_1")
    try:
        conversation_archive.archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            account_id="acc_1",
            method="POST",
            url="https://chatgpt.com/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer secret",
                "api-key": "bare-secret",
                "api_key": "underscore-secret",
                "session_id": "sid_1",
                "x-session-token": "tok_1",
            },
            payload={"model": "gpt-5.4", "input": "привет без юникод-эскейпов"},
        )
    finally:
        reset_request_id(token)
    conversation_archive.flush_archive_writer()

    [record] = _archive_records(tmp_path)
    assert record["request_id"] == "req_archive_1"
    assert record["direction"] == "codex_to_server"
    assert record["kind"] == "responses"
    assert record["transport"] == "http"
    assert record["account_id"] == "acc_1"
    assert record["payload"] == {"model": "gpt-5.4", "input": "привет без юникод-эскейпов"}
    assert record["headers"] == {
        "Authorization": "[redacted]",
        "api-key": "[redacted]",
        "api_key": "[redacted]",
        "session_id": "sid_1",
        "x-session-token": "[redacted]",
    }
    [line] = _archive_lines(tmp_path)
    assert "привет без юникод-эскейпов" in line
    assert "\\u043f" not in line


def test_archive_bytes_disabled_does_not_encode_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=False, directory=tmp_path),
    )

    def fail_b64encode(_data: bytes) -> bytes:
        raise AssertionError("disabled archiving should not encode binary payloads")

    monkeypatch.setattr(conversation_archive.base64, "b64encode", fail_b64encode)

    conversation_archive.archive_bytes(
        direction="server_to_codex",
        kind="responses",
        transport="websocket",
        data=b"large binary frame",
    )

    assert list(tmp_path.iterdir()) == []


def test_archive_queue_is_bounded_and_waits_without_sync_backpressure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    bounded_queue: queue.Queue[tuple[Path, dict[str, object], int] | None] = queue.Queue(maxsize=1)
    bounded_queue.put((tmp_path / "blocked.jsonl.gz", {"payload": "queued"}, 10))
    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", bounded_queue)
    monkeypatch.setattr(conversation_archive, "_ensure_writer_thread", lambda: None)

    def drain_existing_item() -> None:
        time.sleep(0.01)
        bounded_queue.get()
        bounded_queue.task_done()

    drainer = threading.Thread(target=drain_existing_item)
    drainer.start()
    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "синхронный fallback"},
    )
    drainer.join(timeout=1.0)

    assert list(tmp_path.glob("*.jsonl.gz")) == []
    assert bounded_queue.qsize() == 1
    queued_item = bounded_queue.get_nowait()
    assert queued_item is not None
    _path, record, _queued_bytes = queued_item
    assert record["payload"] == {"text": "синхронный fallback"}


def test_archive_queue_byte_limit_preserves_oversized_record_with_backpressure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path, queue_max_bytes=64),
    )
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "x" * 256},
    )
    conversation_archive.flush_archive_writer()

    [record] = _archive_records(tmp_path)
    assert record["payload"] == {"text": "x" * 256}
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == 0


def test_archive_queue_byte_limit_waits_before_queueing_second_oversized_record(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path, queue_max_bytes=64),
    )
    queued_records: queue.Queue[tuple[Path, dict[str, object], int] | None] = queue.Queue()
    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", queued_records)
    monkeypatch.setattr(conversation_archive, "_ensure_writer_thread", lambda: None)
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "x" * 256},
    )

    first_item = queued_records.get_nowait()
    assert first_item is not None
    assert first_item[1]["payload"] == {"text": "x" * 256}
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == first_item[2]

    second_started = threading.Event()

    def enqueue_second() -> None:
        second_started.set()
        conversation_archive.archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload={"text": "y" * 256},
        )

    second_thread = threading.Thread(target=enqueue_second)
    second_thread.start()
    assert second_started.wait(timeout=1.0)
    time.sleep(0.05)
    assert queued_records.empty()
    assert second_thread.is_alive()

    conversation_archive._release_archive_queue_bytes(first_item[2])
    second_thread.join(timeout=1.0)
    assert not second_thread.is_alive()

    second_item = queued_records.get_nowait()
    assert second_item is not None
    assert second_item[1]["payload"] == {"text": "y" * 256}
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == second_item[2]

    conversation_archive._release_archive_queue_bytes(second_item[2])


def test_archive_queue_byte_limit_warning_is_rate_limited(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path, queue_max_bytes=64),
    )
    monkeypatch.setattr(conversation_archive, "_archive_disk_pressure_active", lambda: False)
    monkeypatch.setattr(conversation_archive, "_reserve_archive_queue_bytes", lambda _size: False)
    monkeypatch.setattr(conversation_archive, "_reserve_archive_queue_bytes_blocking", lambda _size: None)
    monotonic_values = iter([100.0, 101.0, 102.0, 161.0])
    monkeypatch.setattr(conversation_archive.time, "monotonic", lambda: next(monotonic_values))
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0
    with conversation_archive._BACKPRESSURE_WARNING_LOCK:
        conversation_archive._BACKPRESSURE_LAST_WARNING_AT = (
            -conversation_archive._BACKPRESSURE_WARNING_INTERVAL_SECONDS
        )
        conversation_archive._BACKPRESSURE_SUPPRESSED_WARNINGS = 0

    caplog.set_level("WARNING", logger="app.core.conversation_archive")
    for idx in range(4):
        conversation_archive.archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload={"text": f"{idx}-" + ("x" * 256)},
        )
    conversation_archive.flush_archive_writer()

    warnings = [
        record
        for record in caplog.records
        if "Conversation archive writer queue byte budget is full" in record.getMessage()
    ]
    assert len(warnings) == 2
    assert warnings[0].suppressed_warnings == 0
    assert warnings[1].suppressed_warnings == 2


def test_archive_writer_streams_gzip_without_precompressing_record(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    def fail_compress(_data: bytes) -> bytes:
        raise AssertionError("normal archive writes should stream gzip data")

    monkeypatch.setattr(conversation_archive.gzip, "compress", fail_compress)
    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "x" * 1024},
    )
    conversation_archive.flush_archive_writer()

    [record] = _archive_records(tmp_path)
    assert record["payload"] == {"text": "x" * 1024}


def test_archive_writer_batches_queued_records(monkeypatch, tmp_path):
    archive_queue: queue.Queue[tuple[Path, dict[str, object], int] | None] = queue.Queue()
    first: tuple[Path, dict[str, object], int] = (tmp_path / "archive.jsonl.gz", {"payload": "one"}, 11)
    second: tuple[Path, dict[str, object], int] = (tmp_path / "archive.jsonl.gz", {"payload": "two"}, 13)
    archive_queue.put(first)
    archive_queue.put(second)
    archive_queue.put(None)
    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", archive_queue)
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        setattr(conversation_archive, "_WRITE_QUEUE_BYTES", 24)

    batches: list[list[tuple[Path, dict[str, object], int]]] = []

    def capture_batch(items: Sequence[tuple[Path, dict[str, object], int]]) -> None:
        batches.append(list(items))

    monkeypatch.setattr(conversation_archive, "_append_records", capture_batch)

    conversation_archive._writer_loop()

    assert [[item[1]["payload"] for item in batch] for batch in batches] == [["one", "two"]]
    assert archive_queue.unfinished_tasks == 0
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == 0


def test_archive_queue_thread_start_failure_drops_record(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        conversation_archive._WRITE_QUEUE_BYTES = 0

    def fail_writer_thread() -> None:
        raise RuntimeError("thread limit reached")

    monkeypatch.setattr(conversation_archive, "_ensure_writer_thread", fail_writer_thread)

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "optional archive"},
    )

    assert list(tmp_path.glob("*.jsonl.gz")) == []
    with conversation_archive._WRITE_QUEUE_BYTES_LOCK:
        assert conversation_archive._WRITE_QUEUE_BYTES == 0


def test_archive_stop_writer_drains_queue_before_sentinel(monkeypatch):
    events: list[str] = []

    class FakeQueue:
        def join(self) -> None:
            events.append("queue.join")

        def put(self, item: object) -> None:
            assert item is None
            events.append("queue.put_sentinel")

    class FakeThread:
        def join(self, timeout: float | None = None) -> None:
            assert timeout is None
            events.append("thread.join")

    monkeypatch.setattr(conversation_archive, "_WRITE_QUEUE", FakeQueue())
    monkeypatch.setattr(conversation_archive, "_WRITER_THREAD", FakeThread())

    conversation_archive._stop_writer()

    assert events == ["queue.join", "queue.put_sentinel", "thread.join"]
