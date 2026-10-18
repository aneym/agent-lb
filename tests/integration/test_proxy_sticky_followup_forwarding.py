from __future__ import annotations

from datetime import timezone

import pytest

import app.modules.proxy.service as proxy_module
from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.usage.repository import UsageRepository
from tests.integration.proxy_sticky_sessions_support import (
    _import_account,
    _install_proxy_settings_cache,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_backend_responses_http_forwards_previous_response_id(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_id = await _import_account(async_client, "acc_prev_http_1", "prev_http_1@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    seen_prev_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del headers, access_token, account_id, base_url, raise_for_status, _kw
        seen_prev_ids.append(getattr(payload, "previous_response_id", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_prev_http"}}\\n\\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "hi",
            "previous_response_id": "resp_prev_http_123",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            "stream": True,
        },
        headers={"session_id": "backend-thread-prev-http-123"},
    )
    assert response.status_code == 200
    assert seen_prev_ids == ["resp_prev_http_123"]


@pytest.mark.asyncio
async def test_v1_responses_http_forwards_previous_response_id(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_id = await _import_account(async_client, "acc_v1_prev_http_1", "v1_prev_http_1@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    seen_prev_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del headers, access_token, account_id, base_url, raise_for_status, _kw
        seen_prev_ids.append(getattr(payload, "previous_response_id", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_prev_http"}}\\n\\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": "continue",
            "previous_response_id": "resp_prev_v1_http_123",
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert seen_prev_ids == ["resp_prev_v1_http_123"]
