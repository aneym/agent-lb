from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

from app.modules.conversation_archive import service as conversation_archive_service
from tests.floor.conversation_archive_support import (
    _ArchiveSettings,
    _write_gzip_record,
)


def test_archive_service_reads_gzip_and_legacy_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    legacy_path = tmp_path / "2026-04-28.jsonl"
    legacy_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-28T10:00:00+00:00",
                "request_id": "req_legacy",
                "direction": "codex_to_server",
                "kind": "responses",
                "transport": "http",
                "payload": {"input": "old"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    gzip_path = tmp_path / "2026-04-29T10.jsonl.gz"
    with gzip.open(gzip_path, "wt", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp": "2026-04-29T10:00:00+00:00",
                    "request_id": "req_gzip",
                    "direction": "server_to_codex",
                    "kind": "responses",
                    "transport": "sse",
                    "payload": {"type": "response.completed"},
                }
            )
            + "\n"
        )

    files = conversation_archive_service.list_archive_files()
    assert [file.name for file in files] == ["2026-04-29T10.jsonl.gz", "2026-04-28.jsonl"]
    assert [file.date for file in files] == ["2026-04-29T10", "2026-04-28"]
    assert files[0].compressed is True
    assert files[1].compressed is False

    page = conversation_archive_service.read_archive_records(
        filename="2026-04-29T10.jsonl.gz",
        limit=10,
        offset=0,
        direction="server_to_codex",
    )
    assert page.total == 1
    assert page.has_more is False
    assert page.records[0]["request_id"] == "req_gzip"
    assert page.records[0]["_archive_file"] == "2026-04-29T10.jsonl.gz"

    legacy_page = conversation_archive_service.read_archive_records(
        filename="2026-04-28.jsonl",
        limit=10,
        offset=0,
        request_id="req_legacy",
    )
    assert legacy_page.total == 1
    assert legacy_page.records[0]["payload"] == {"input": "old"}

    all_files_page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        request_id="req_gzip",
    )
    assert [record["request_id"] for record in all_files_page.records] == ["req_gzip"]

    requested_at_page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        request_id="req_gzip",
        requested_at=datetime.fromisoformat("2026-04-29T10:30:00+00:00"),
    )
    assert [record["request_id"] for record in requested_at_page.records] == ["req_gzip"]


def test_archive_service_lookup_requests_adjacent_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    _write_gzip_record(
        tmp_path / "2026-04-29T10.jsonl.gz",
        {
            "timestamp": "2026-04-29T10:59:45+00:00",
            "request_id": "req_prev_hour",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "prev"},
        },
    )
    _write_gzip_record(
        tmp_path / "2026-04-29T11.jsonl.gz",
        {
            "timestamp": "2026-04-29T11:00:15+00:00",
            "request_id": "req_next_hour",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "next"},
        },
    )

    boundary_requested_at = datetime.fromisoformat("2026-04-29T11:00:00+00:00")
    page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        requested_at=boundary_requested_at,
    )

    assert page.total == 2
    assert [record["request_id"] for record in page.records] == [
        "req_prev_hour",
        "req_next_hour",
    ]
    assert [record["_archive_file"] for record in page.records] == [
        "2026-04-29T10.jsonl.gz",
        "2026-04-29T11.jsonl.gz",
    ]


def test_archive_service_lookup_request_id_searches_beyond_adjacent_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    _write_gzip_record(
        tmp_path / "2026-04-29T10.jsonl.gz",
        {
            "timestamp": "2026-04-29T10:59:45+00:00",
            "request_id": "req_span",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "near"},
        },
    )
    _write_gzip_record(
        tmp_path / "2026-04-29T11.jsonl.gz",
        {
            "timestamp": "2026-04-29T11:00:15+00:00",
            "request_id": "req_other",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "near"},
        },
    )
    _write_gzip_record(
        tmp_path / "2026-04-29T12.jsonl.gz",
        {
            "timestamp": "2026-04-29T12:01:05+00:00",
            "request_id": "req_span",
            "direction": "server_to_codex",
            "kind": "responses",
            "transport": "http",
            "payload": {"input": "later"},
        },
    )

    page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        request_id="req_span",
        requested_at=datetime.fromisoformat("2026-04-29T10:30:00+00:00"),
    )

    assert page.total == 2
    assert [record["request_id"] for record in page.records] == ["req_span", "req_span"]
    assert [record["_archive_file"] for record in page.records] == [
        "2026-04-29T10.jsonl.gz",
        "2026-04-29T12.jsonl.gz",
    ]


def test_archive_service_lookup_request_id_includes_adjacent_day_legacy_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    (tmp_path / "2026-04-29.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-04-29T23:59:58+00:00",
                "request_id": "req_midnight",
                "direction": "server_to_codex",
                "kind": "responses",
                "transport": "http",
                "payload": {"input": "end_of_day"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    page = conversation_archive_service.read_archive_records(
        filename=None,
        limit=10,
        offset=0,
        request_id="req_midnight",
        requested_at=datetime.fromisoformat("2026-04-30T00:10:00+00:00"),
    )

    assert page.total == 1
    assert page.records[0]["request_id"] == "req_midnight"
    assert page.records[0]["_archive_file"] == "2026-04-29.jsonl"


def test_archive_service_expands_user_home(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    archive_path = archive_dir / "2026-04-29T10.jsonl.gz"
    with gzip.open(archive_path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": "2026-04-29T10:00:00+00:00"}) + "\n")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=Path("~/archive")),
    )

    [file] = conversation_archive_service.list_archive_files()
    assert file.name == archive_path.name


def test_archive_service_keeps_readable_records_before_corrupt_gzip_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )
    path = tmp_path / "2026-04-30.jsonl.gz"
    path.write_bytes(
        gzip.compress(json.dumps({"request_id": "req_ok", "direction": "codex_to_server"}).encode("utf-8") + b"\n")
        + b"not a complete gzip member"
    )

    page = conversation_archive_service.read_archive_records(
        filename=path.name,
        limit=10,
        offset=0,
    )

    assert [record["request_id"] for record in page.records] == ["req_ok"]


def test_archive_service_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conversation_archive_service,
        "get_settings",
        lambda: _ArchiveSettings(enabled=True, directory=tmp_path),
    )

    try:
        conversation_archive_service.read_archive_records(
            filename="../2026-04-29.jsonl.gz",
            limit=10,
            offset=0,
        )
    except conversation_archive_service.ConversationArchiveInvalidFileError:
        pass
    else:
        raise AssertionError("expected invalid archive file error")
