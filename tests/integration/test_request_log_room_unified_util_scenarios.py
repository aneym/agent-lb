"""Request-boundary room attribution and upstream quota evidence scenarios."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select, text

import app.modules.proxy.anthropic_service as anthropic_proxy_module
import app.modules.proxy.service as proxy_module
from app.db.migrate import _build_alembic_config, run_upgrade
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.request_logs.mappers import to_request_log_entry
from tests.integration.test_anthropic_proxy import (
    ANTHROPIC_JSON_BYTES,
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration

UTIL_5H = "anthropic-ratelimit-unified-5h-utilization"
UTIL_7D = "anthropic-ratelimit-unified-7d-utilization"
PAYLOAD = {
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 32,
    "messages": [{"role": "user", "content": "hello"}],
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream,room,upstream_headers,expected",
    [
        (False, "Room-Alpha", {UTIL_5H: "0.37", UTIL_7D: "0.81"}, ("room-alpha", 0.37, 0.81)),
        (True, "Room-Alpha", {UTIL_5H: "0.37", UTIL_7D: "0.81"}, ("room-alpha", 0.37, 0.81)),
        (False, None, {}, (None, None, None)),
        (False, "Bad Room!", {UTIL_5H: "abc", UTIL_7D: "nan"}, (None, None, None)),
        (False, "Bad Room!", {UTIL_5H: "-1", UTIL_7D: "inf"}, (None, None, None)),
        (False, "  R1@host/path:unit._-  ", {UTIL_5H: "0", UTIL_7D: "1.2"}, ("r1@host/path:unit._-", 0.0, 1.2)),
        (False, "r" * 128, {}, ("r" * 128, None, None)),
        (False, "r" * 129, {}, (None, None, None)),
    ],
    ids=["S1-json", "S2-stream", "S3-missing", "S4-invalid", "S4-negative", "raw-normalized", "room-max", "room-long"],
)
async def test_anthropic_room_and_utilization(async_client, monkeypatch, stream, room, upstream_headers, expected):
    await _insert_account(
        account_id="anthropic-room", provider="anthropic", access_token="fixture-access", email="fixture@example.com"
    )
    body = ANTHROPIC_SSE_BYTES if stream else ANTHROPIC_JSON_BYTES

    def open_response(self, session, *, provider_name, headers, json_body):
        return _FakeResponseContext(_FakeResponse(200, body, upstream_headers))

    monkeypatch.setattr(anthropic_proxy_module.AnthropicProxyService, "_open_upstream_response", open_response)
    headers = {"x-request-id": "room-scenario"}
    if room is not None:
        headers["x-agent-lb-room"] = room
    response = await async_client.post("/v1/messages", json={**PAYLOAD, "stream": stream}, headers=headers)
    assert response.status_code == 200
    assert response.content == body
    async with SessionLocal() as session:
        row = (await session.execute(select(RequestLog).where(RequestLog.request_id == "room-scenario"))).scalar_one()
        assert (row.room, row.unified_5h_utilization, row.unified_7d_utilization) == expected
        entry = to_request_log_entry(row)
        assert (entry.room, entry.unified_5h_utilization, entry.unified_7d_utilization) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
async def test_S5_upstream_429_keeps_error_attempt_utilization(async_client, monkeypatch, stream):
    await _insert_account(
        account_id="anthropic-room", provider="anthropic", access_token="fixture-access", email="fixture@example.com"
    )

    def open_response(self, session, *, provider_name, headers, json_body):
        return _FakeResponseContext(_FakeResponse(429, b'{"error":{"message":"rate limited"}}', {UTIL_5H: "1.0"}))

    monkeypatch.setattr(anthropic_proxy_module.AnthropicProxyService, "_open_upstream_response", open_response)
    response = await async_client.post(
        "/v1/messages", json={**PAYLOAD, "stream": stream}, headers={"x-request-id": "room-429"}
    )
    # Streaming errors are vendor-native SSE after the 200 response has started.
    assert response.status_code == (200 if stream else 429)
    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog).where(RequestLog.request_id == "room-429"))).scalars().all()
        errors = [row for row in rows if row.error_code == "rate_limit_exceeded"]
        assert len(errors) == 1
        assert errors[0].status == "error"
        assert errors[0].unified_5h_utilization == 1.0
        assert errors[0].unified_7d_utilization is None
        # Pool-level errors are not upstream responses and must not inherit old headers.
        assert all(row.unified_5h_utilization is None for row in rows if row not in errors)


@pytest.mark.asyncio
async def test_S6_codex_room_and_context_reset(async_client, monkeypatch):
    await _insert_account(
        account_id="codex-room", provider="openai", access_token="fixture-access", email="fixture@example.com"
    )
    captured = []

    async def stream_response(payload, headers, access_token, account_id, **kwargs):
        captured.append(dict(headers))
        yield 'data: {"type":"response.completed","response":{"id":"' + headers["x-request-id"] + '"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", stream_response)
    for rid, headers in [("codex-room", {"x-agent-lb-room": "r1"}), ("codex-no-room", {})]:
        response = await async_client.post(
            "/backend-api/codex/responses",
            json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
            headers={"x-request-id": rid, **headers},
        )
        assert response.status_code == 200
    assert len(captured) == 2
    assert all("x-agent-lb-room" not in headers for headers in captured)
    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog))).scalars().all()
        assert {row.request_id: (row.room, row.unified_5h_utilization, row.unified_7d_utilization) for row in rows} == {
            "codex-room": ("r1", None, None),
            "codex-no-room": (None, None, None),
        }


@pytest.mark.asyncio
async def test_S7_room_does_not_change_response_or_upstream_request(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-room", provider="anthropic", access_token="fixture-access", email="fixture@example.com"
    )
    captured = []

    def open_response(self, session, *, provider_name, headers, json_body):
        captured.append((dict(headers), json_body))
        return _FakeResponseContext(
            _FakeResponse(
                200, ANTHROPIC_JSON_BYTES, {UTIL_5H: "0.37", UTIL_7D: "0.81", "content-type": "application/json"}
            )
        )

    monkeypatch.setattr(anthropic_proxy_module.AnthropicProxyService, "_open_upstream_response", open_response)
    responses = []
    for headers in [{"x-agent-lb-room": "Room-Alpha"}, {}]:
        responses.append(
            await async_client.post(
                "/v1/messages", json=PAYLOAD, headers={"x-request-id": "response-parity", **headers}
            )
        )
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].content == responses[1].content == ANTHROPIC_JSON_BYTES
    assert responses[0].headers == responses[1].headers
    assert captured[0] == captured[1]
    assert all("x-agent-lb-room" not in headers for headers, _ in captured)
    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(RequestLog).where(RequestLog.request_id == "response-parity")))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert sorted(row.room or "" for row in rows) == ["", "room-alpha"]
    assert "x-agent-lb-room" not in responses[0].headers


def test_room_utilization_migration_preserves_rows_and_round_trips(tmp_path):
    parent = "20261001_180000_add_request_log_caller_seat"
    url = f"sqlite:///{tmp_path / 'room-utilization.sqlite'}"
    config = _build_alembic_config(url)
    run_upgrade(url, parent, bootstrap_legacy=False)
    engine = create_engine(url)
    fields = {"room", "unified_5h_utilization", "unified_7d_utilization"}
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO request_logs (request_id, model, status, caller_seat) "
                    "VALUES ('old', 'model', 'ok', 'seat')"
                )
            )
        for _ in range(2):
            run_upgrade(url, "head", bootstrap_legacy=False)
            with engine.begin() as connection:
                columns = {column["name"]: column for column in inspect(connection).get_columns("request_logs")}
                assert fields <= columns.keys()
                assert all(columns[name]["nullable"] for name in fields)
                assert connection.execute(
                    text("SELECT caller_seat, room, unified_5h_utilization, unified_7d_utilization FROM request_logs")
                ).one() == ("seat", None, None, None)
                connection.execute(
                    text("UPDATE request_logs SET room='r1', unified_5h_utilization=0.37, unified_7d_utilization=0.81")
                )
            # Replay the migration against already-present columns.
            command.stamp(config, parent)
            run_upgrade(url, "head", bootstrap_legacy=False)
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT room, unified_5h_utilization, unified_7d_utilization FROM request_logs")
                ).one() == ("r1", 0.37, 0.81)
            command.downgrade(config, parent)
            with engine.connect() as connection:
                assert not fields & {column["name"] for column in inspect(connection).get_columns("request_logs")}
                assert connection.execute(text("SELECT request_id, caller_seat FROM request_logs")).one() == (
                    "old",
                    "seat",
                )
    finally:
        engine.dispose()
