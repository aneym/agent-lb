from __future__ import annotations

from datetime import timedelta, timezone

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
async def test_v1_session_id_does_not_create_durable_codex_session_affinity(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_v1_sid_a", "v1_sid_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_v1_sid_b", "v1_sid_b@example.com")

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
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_session"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    headers = {"session_id": "v1-thread-123"}
    stream_payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "stream": True,
    }
    response = await async_client.post("/v1/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_sid_a"]

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
        "input": "hello",
    }
    response = await async_client.post("/v1/responses/compact", json=compact_payload, headers=headers)
    assert response.status_code == 200
    assert compact_seen == ["acc_v1_sid_a"]

    async with SessionLocal() as session:
        codex_row = (
            await session.execute(
                text("SELECT kind FROM sticky_sessions WHERE key = :key"),
                {"key": "v1-thread-123"},
            )
        ).fetchone()
        assert codex_row is None


@pytest.mark.asyncio
async def test_v1_prompt_cache_key_reuses_recent_responses_and_compact_without_sticky_threads(
    async_client,
    monkeypatch,
):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    _install_proxy_settings_cache(
        monkeypatch,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=60,
    )
    acc_a_id = await _import_account(async_client, "acc_v1_cache_a", "v1_cache_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_v1_cache_b", "v1_cache_b@example.com")

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
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_cache"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    thread_key = "v1-cache-thread-123"
    stream_payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "stream": True,
        "prompt_cache_key": thread_key,
    }
    response = await async_client.post("/v1/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_cache_a"]

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
        "input": "hello",
        "prompt_cache_key": thread_key,
    }
    response = await async_client.post("/v1/responses/compact", json=compact_payload)
    assert response.status_code == 200
    assert compact_seen == ["acc_v1_cache_a"]

    response = await async_client.post("/v1/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_cache_a", "acc_v1_cache_a"]


@pytest.mark.asyncio
async def test_v1_responses_derives_prompt_cache_key_when_absent(async_client, monkeypatch):
    _install_proxy_settings_cache(
        monkeypatch,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=60,
    )
    acc_a_id = await _import_account(async_client, "acc_v1_derived_a", "v1_derived_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_v1_derived_b", "v1_derived_b@example.com")

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

    seen_keys: list[str | None] = []
    seen_accounts: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen_accounts.append(account_id)
        seen_keys.append(getattr(payload, "prompt_cache_key", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_derived"}}\\n\\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hello", "stream": True}
    response = await async_client.post("/v1/responses", json=payload)
    assert response.status_code == 200
    assert seen_accounts == ["acc_v1_derived_a"]
    assert isinstance(seen_keys[0], str)
    assert seen_keys[0]


@pytest.mark.asyncio
async def test_v1_prompt_cache_key_rebalances_after_affinity_expires(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    _install_proxy_settings_cache(
        monkeypatch,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=60,
    )
    acc_a_id = await _import_account(async_client, "acc_v1_expire_a", "v1_expire_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_v1_expire_b", "v1_expire_b@example.com")

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
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_cache_expire"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    thread_key = "v1-cache-expire-thread-123"
    stream_payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "stream": True,
        "prompt_cache_key": thread_key,
    }
    response = await async_client.post("/v1/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_expire_a"]

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
        stale_updated_at = utcnow() - timedelta(minutes=10)
        await session.execute(
            text(
                """
                UPDATE sticky_sessions
                SET updated_at = :stale_updated_at
                WHERE key = :sticky_key AND kind = 'prompt_cache'
                """
            ),
            {"sticky_key": thread_key, "stale_updated_at": stale_updated_at},
        )
        await session.commit()

    response = await async_client.post("/v1/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_expire_a", "acc_v1_expire_b"]


@pytest.mark.asyncio
async def test_v1_auto_derived_key_separates_parallel_sessions(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_par_a", "par_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_par_b", "par_b@example.com")

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

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_p"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    session_a = {"model": "gpt-5.1", "input": "build a server", "stream": True}
    session_b = {"model": "gpt-5.1", "input": "write tests", "stream": True}

    await async_client.post("/v1/responses", json=session_a)
    await async_client.post("/v1/responses", json=session_b)

    assert len(seen) == 2
    assert seen[0] == "acc_par_a"


@pytest.mark.asyncio
async def test_v1_auto_derived_key_stable_across_turns(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_turn_a", "turn_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_turn_b", "turn_b@example.com")

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

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_t"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    turn1 = {
        "model": "gpt-5.1",
        "input": [{"role": "user", "content": "build a server"}],
        "stream": True,
    }
    turn2 = {
        "model": "gpt-5.1",
        "input": [
            {"role": "user", "content": "build a server"},
            {"role": "assistant", "content": "Here is a server..."},
            {"role": "user", "content": "add logging"},
        ],
        "stream": True,
    }

    await async_client.post("/v1/responses", json=turn1)
    assert seen == ["acc_turn_a"]

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=90.0,
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

    await async_client.post("/v1/responses", json=turn2)

    assert seen == ["acc_turn_a", "acc_turn_a"]
