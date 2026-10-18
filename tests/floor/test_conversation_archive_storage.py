from __future__ import annotations

import base64
import errno
import gzip
import json
import os
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from app.core import conversation_archive
from app.modules.conversation_archive import api as conversation_archive_api
from app.modules.conversation_archive import service as conversation_archive_service
from tests.floor.conversation_archive_support import (
    _archive_records,
    _ArchiveSettings,
    _reset_archive_disk_pressure,
)


def test_archive_file_listing_runs_sync(monkeypatch):
    called: list[object] = []
    modified_at = datetime.fromisoformat("2026-05-16T00:00:00+00:00")
    archive_file = conversation_archive_service.ConversationArchiveFile(
        name="2026-05-16T00.jsonl.gz",
        date="2026-05-16T00",
        size_bytes=123,
        compressed=True,
        modified_at=modified_at,
    )

    monkeypatch.setattr(
        conversation_archive_api.service,
        "list_archive_files",
        lambda: called.append("service") or [archive_file],
    )

    [response] = conversation_archive_api.list_conversation_archive_files()

    assert called == ["service"]
    assert response.name == "2026-05-16T00.jsonl.gz"
    assert response.modified_at == modified_at


def test_archive_appends_complete_gzip_members(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "first"},
    )
    conversation_archive.archive_json(
        direction="server_to_codex",
        kind="responses",
        transport="http",
        payload={"text": "second"},
    )
    conversation_archive.flush_archive_writer()

    records = _archive_records(tmp_path)
    assert [record["payload"] for record in records] == [{"text": "first"}, {"text": "second"}]


def test_archive_uses_hourly_gzip_files(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    conversation_archive.archive_json(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        payload={"text": "hourly"},
    )
    conversation_archive.flush_archive_writer()

    [path] = list(tmp_path.glob("*.jsonl.gz"))
    assert len(path.stem) == len("2026-04-30T12.jsonl")
    assert path.name.endswith(".jsonl.gz")
    assert "T" in path.name


def test_archive_files_are_operator_only_even_with_permissive_umask(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=archive_dir),
    )
    old_umask = os.umask(0o022)
    try:
        conversation_archive.archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload={"text": "private"},
        )
        conversation_archive.flush_archive_writer()
    finally:
        os.umask(old_umask)

    [path] = list(archive_dir.glob("*.jsonl.gz"))
    assert stat.S_IMODE(archive_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_archive_path_expands_user_home(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=Path("~/archive")),
    )

    assert conversation_archive._archive_path().parent == archive_dir


def test_archive_writer_recovers_corrupt_gzip_tail_before_append(tmp_path):
    path = tmp_path / "2026-04-30.jsonl.gz"
    conversation_archive._RECOVERY_CHECKED_PATHS.discard(path.resolve())
    path.write_bytes(
        gzip.compress(json.dumps({"request_id": "req_ok", "payload": "old"}).encode("utf-8") + b"\n")
        + b"not a complete gzip member"
    )

    conversation_archive._append_record(path, {"request_id": "req_new", "payload": "new"})

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh.read().splitlines()]
    assert [row["request_id"] for row in rows] == ["req_ok", "req_new"]
    backups = list(tmp_path.glob("2026-04-30.jsonl.gz.corrupt-*"))
    assert len(backups) == 1


def test_archive_recovery_check_runs_once_per_file(monkeypatch, tmp_path):
    path = tmp_path / "2026-04-30T12.jsonl.gz"
    conversation_archive._RECOVERY_CHECKED_PATHS.discard(path.resolve())
    path.write_bytes(gzip.compress(json.dumps({"request_id": "req_ok"}).encode("utf-8") + b"\n"))
    calls = 0

    def fake_readable(read_path: Path) -> bool:
        nonlocal calls
        assert read_path == path
        calls += 1
        return True

    monkeypatch.setattr(conversation_archive, "_gzip_archive_is_readable", fake_readable)

    conversation_archive._append_record(path, {"request_id": "req_new_1"})
    conversation_archive._append_record(path, {"request_id": "req_new_2"})

    assert calls == 1


def test_archive_write_failure_forgets_prior_recovery_check(monkeypatch, tmp_path):
    path = tmp_path / "2026-04-30T12.jsonl.gz"
    path.write_bytes(gzip.compress(json.dumps({"request_id": "req_ok"}).encode("utf-8") + b"\n"))
    conversation_archive._RECOVERY_CHECKED_PATHS.discard(path.resolve())

    conversation_archive._append_record(path, {"request_id": "req_new_1"})
    assert path.resolve() in conversation_archive._RECOVERY_CHECKED_PATHS

    def fail_write(_path: Path, _record: object) -> None:
        raise OSError("simulated partial archive write")

    monkeypatch.setattr(conversation_archive, "_write_gzip_jsonl_record", fail_write)
    conversation_archive._append_record(path, {"request_id": "req_new_2"})

    assert path.resolve() not in conversation_archive._RECOVERY_CHECKED_PATHS


def test_archive_batch_write_failure_does_not_drop_other_paths(monkeypatch, tmp_path):
    bad_path = tmp_path / "bad" / "2026-04-30T12.jsonl.gz"
    good_path = tmp_path / "good" / "2026-04-30T12.jsonl.gz"

    def write_record(path: Path, record: Mapping[str, object]) -> None:
        if path == bad_path:
            raise OSError("simulated archive write failure")
        conversation_archive._write_gzip_jsonl_records(path, [record])

    monkeypatch.setattr(conversation_archive, "_write_gzip_jsonl_record", write_record)

    conversation_archive._append_records(
        [
            (bad_path, {"request_id": "req_bad"}, 0),
            (good_path, {"request_id": "req_good"}, 0),
        ]
    )

    assert not bad_path.exists()
    with gzip.open(good_path, "rt", encoding="utf-8") as handle:
        assert json.loads(handle.readline()) == {"request_id": "req_good"}


def test_archive_disk_full_pauses_writes_without_traceback_spam(monkeypatch, tmp_path, caplog):
    _reset_archive_disk_pressure()
    path = tmp_path / "2026-04-30T12.jsonl.gz"

    def fail_write(_path: Path, _record: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(conversation_archive, "_write_gzip_jsonl_record", fail_write)
    caplog.set_level("WARNING", logger="app.core.conversation_archive")

    conversation_archive._append_record(path, {"request_id": "req_full", "payload": "old"})

    assert not path.exists()
    assert "Conversation archive disk pressure detected; pausing archive writes" in caplog.text
    assert "Traceback" not in caplog.text
    assert conversation_archive._archive_disk_pressure_active() is True

    calls = 0

    def record_write(_path: Path, _record: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(conversation_archive, "_write_gzip_jsonl_record", record_write)
    conversation_archive._append_record(path, {"request_id": "req_dropped", "payload": "new"})
    assert calls == 0

    with conversation_archive._DISK_PRESSURE_LOCK:
        conversation_archive._DISK_PRESSURE_PAUSED_UNTIL = 0.0
    conversation_archive._append_record(path, {"request_id": "req_retry", "payload": "new"})
    assert calls == 1


def test_archive_enqueue_drops_records_while_disk_pressure_pause_is_active(monkeypatch, tmp_path):
    _reset_archive_disk_pressure()
    with conversation_archive._DISK_PRESSURE_LOCK:
        conversation_archive._DISK_PRESSURE_PAUSED_UNTIL = float(conversation_archive.time.monotonic() + 60)
    monkeypatch.setattr(
        conversation_archive,
        "_ensure_writer_thread",
        lambda: (_ for _ in ()).throw(AssertionError("writer should not start while paused")),
    )

    conversation_archive._enqueue_record(tmp_path / "paused.jsonl.gz", {"request_id": "req_paused"})

    assert list(tmp_path.iterdir()) == []
    _reset_archive_disk_pressure()


def test_archive_disk_pressure_detection_walks_exception_causes():
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = OSError(errno.EDQUOT, "Disk quota exceeded")

    assert conversation_archive._is_disk_pressure_error(wrapped) is True
    assert conversation_archive._is_disk_pressure_error(RuntimeError("database or disk is full")) is True
    assert conversation_archive._is_disk_pressure_error(RuntimeError("regular archive failure")) is False


def test_archive_disabled_does_not_create_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=False, directory=tmp_path),
    )

    conversation_archive.archive_text(
        direction="server_to_codex",
        kind="responses",
        transport="websocket",
        text='{"type":"response.completed"}',
    )
    conversation_archive.flush_archive_writer()

    assert list(tmp_path.glob("*.jsonl*")) == []


def test_archive_bytes_uses_base64_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    conversation_archive.archive_bytes(
        direction="server_to_codex",
        kind="responses",
        transport="websocket",
        data=b"\x00\x01binary",
    )
    conversation_archive.flush_archive_writer()

    [record] = _archive_records(tmp_path)
    payload = cast(dict[str, object], record["payload"])
    assert payload["encoding"] == "base64"
    assert payload["data"] == base64.b64encode(b"\x00\x01binary").decode("ascii")
