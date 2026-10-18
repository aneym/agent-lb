from __future__ import annotations

from datetime import timezone

import pytest
from sqlalchemy import text

import app.modules.proxy.service as proxy_module
from app.core.openai.models import OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.usage.repository import UsageRepository
from tests.integration.proxy_sticky_sessions_support import (
    _import_account,
    _install_proxy_settings_cache,
    _set_routing_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_proxy_codex_session_id_pins_responses_and_compact_without_sticky_threads(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_sid_a", "sid_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_sid_b", "sid_b@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_session"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    headers = {"session_id": "codex-thread-123"}
    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_a"]

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=95.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=5.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    compact_payload = {
        "model": "gpt-5.1",
        "instructions": "summarize",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
    }
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=compact_payload,
        headers=headers,
    )
    assert response.status_code == 200
    assert compact_seen == ["acc_sid_a"]

    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_a", "acc_sid_a"]


@pytest.mark.asyncio
async def test_proxy_codex_session_id_reallocates_when_pinned_budget_exhausted(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_OPENAI_STICKY_HOLD_UNTIL_EXHAUSTED", "false")
    get_settings.cache_clear()
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_sid_budget_a", "sid_budget_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_sid_budget_b", "sid_budget_b@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_session_budget"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    headers = {"session_id": "codex-thread-budget"}
    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_budget_a"]

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=99.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=5.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    compact_payload = {
        "model": "gpt-5.1",
        "instructions": "summarize",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
    }
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=compact_payload,
        headers=headers,
    )
    assert response.status_code == 200
    assert compact_seen == ["acc_sid_budget_b"]

    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_budget_a", "acc_sid_budget_b"]


@pytest.mark.asyncio
async def test_proxy_codex_session_id_compact_first_pins_followup_stream_without_sticky_threads(
    async_client,
    monkeypatch,
):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_sid_compact_a", "sid_compact_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_sid_compact_b", "sid_compact_b@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_compact_first"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    headers = {"session_id": "codex-compact-first-123"}
    compact_payload = {
        "model": "gpt-5.1",
        "instructions": "summarize",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
    }
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=compact_payload,
        headers=headers,
    )
    assert response.status_code == 200
    assert compact_seen == ["acc_sid_compact_a"]

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=95.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=5.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "continue",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_compact_a"]


@pytest.mark.asyncio
async def test_proxy_codex_session_id_switches_when_pinned_rate_limited(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_sid_retry_a", "sid_retry_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_sid_retry_b", "sid_retry_b@example.com")
    upstream_acc_a = "acc_sid_retry_a"
    upstream_acc_b = "acc_sid_retry_b"

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    stream_seen: list[str] = []
    fail_pinned = {"enabled": False}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        if account_id == upstream_acc_a and fail_pinned["enabled"]:
            yield (
                'data: {"type":"response.failed","response":{"error":{"code":"rate_limit_exceeded",'
                '"message":"slow down"}}}\n\n'
            )
            return
        yield 'data: {"type":"response.completed","response":{"id":"resp_session_retry"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    headers = {"session_id": "codex-session-retry-123"}
    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == [upstream_acc_a]

    fail_pinned["enabled"] = True
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200

    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == [upstream_acc_a, upstream_acc_a, upstream_acc_b, upstream_acc_b]


@pytest.mark.asyncio
async def test_backend_codex_session_affinity_also_forwards_prompt_cache_key_when_missing(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_id = await _import_account(async_client, "acc_codex_sid_1", "codex_sid_1@example.com")

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

    seen_keys: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen_keys.append(getattr(payload, "prompt_cache_key", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_backend_codex"}}\\n\\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        },
        headers={"session_id": "backend-thread-123"},
    )
    assert response.status_code == 200
    assert isinstance(seen_keys[0], str)
    assert seen_keys[0]

    async with SessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT kind FROM sticky_sessions WHERE key = :key"),
                {"key": "backend-thread-123"},
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "codex_session"


@pytest.mark.asyncio
async def test_codex_endpoint_uses_prompt_cache_sticky_kind(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=True)
    acc_id = await _import_account(async_client, "acc_kind_a", "kind_a@example.com")

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

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_k"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True, "prompt_cache_key": "pck_abc"}
    await async_client.post("/backend-api/codex/responses", json=payload)
    assert seen == ["acc_kind_a"]

    async with SessionLocal() as session:
        row = (await session.execute(text("SELECT kind FROM sticky_sessions WHERE key = 'pck_abc'"))).fetchone()
        assert row is not None
        assert row[0] == "prompt_cache"
