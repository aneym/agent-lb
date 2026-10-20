from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

import app.modules.proxy.service as proxy_module
from app.core.openai.models import OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from tests.floor.api_keys_api_support import _TEST_MODELS, _import_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/codex/responses/compact", "/v1/responses/compact"])
async def test_api_key_limit_applies_to_compact_responses(async_client, monkeypatch, endpoint):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "compact-usage-limit",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 10},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_compact_usage_key", "compact-usage-key@example.com")

    seen = {"calls": 0}

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        seen["calls"] += 1
        return OpenAIResponsePayload.model_validate(
            {
                "id": "resp_compact_1",
                "model": _TEST_MODELS[0],
                "status": "completed",
                "usage": {
                    "input_tokens": 7,
                    "output_tokens": 5,
                    "total_tokens": 12,
                },
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    request_payload = {
        "model": _TEST_MODELS[0],
        "instructions": "hi",
        "input": [],
    }

    first = await async_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        json=request_payload,
    )
    assert first.status_code == 200

    blocked = await async_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        json=request_payload,
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert seen["calls"] == 1

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 12


@pytest.mark.asyncio
async def test_chat_completions_stream_finalizes_token_limit(async_client, monkeypatch):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "chat-completions-token-limit",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 10},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_chat_token_limit", "chat-token-limit@example.com")

    seen = {"calls": 0}

    async def fake_stream(_payload, _headers, _access_token, _account_id, base_url=None, raise_for_status=False):
        seen["calls"] += 1
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_chat_token_limit","usage":'
            '{"input_tokens":7,"output_tokens":5,"total_tokens":12}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": _TEST_MODELS[0],
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        _ = [line async for line in response.aiter_lines() if line]

    blocked = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": _TEST_MODELS[0],
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert seen["calls"] == 1

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 12


@pytest.mark.asyncio
async def test_api_key_limit_parallel_requests_do_not_exceed_quota(async_client, monkeypatch):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "compact-parallel-limit",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 30},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_compact_parallel", "compact-parallel@example.com")

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        await asyncio.sleep(0.05)
        return OpenAIResponsePayload.model_validate(
            {
                "id": "resp_compact_parallel",
                "model": _TEST_MODELS[0],
                "status": "completed",
                "usage": {
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "total_tokens": 10,
                },
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    async def _send_once():
        return await async_client.post(
            "/v1/responses/compact",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": _TEST_MODELS[0], "instructions": "hi", "input": []},
        )

    responses = await asyncio.gather(*[_send_once() for _ in range(5)])
    allowed = [resp for resp in responses if resp.status_code == 200]
    blocked = [resp for resp in responses if resp.status_code == 429]
    assert len(allowed) >= 1
    assert len(allowed) + len(blocked) == 5

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value <= 30


@pytest.mark.asyncio
async def test_api_key_limit_atomic_with_global_and_model_scope(async_client):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "atomic-global-model",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100},
                {
                    "limitType": "total_tokens",
                    "limitWindow": "weekly",
                    "maxValue": 5,
                    "modelFilter": _TEST_MODELS[0],
                },
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        model_limit = next(limit for limit in limits if limit.model_filter == _TEST_MODELS[0])
        model_limit.current_value = 5
        await session.commit()

    blocked = await async_client.post(
        "/v1/responses/compact",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": _TEST_MODELS[0], "instructions": "hi", "input": []},
    )
    assert blocked.status_code == 429

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        global_limit = next(limit for limit in limits if limit.model_filter is None)
        model_limit = next(limit for limit in limits if limit.model_filter == _TEST_MODELS[0])
        assert global_limit.current_value == 0
        assert model_limit.current_value == 5


@pytest.mark.asyncio
async def test_global_limit_blocks_models_and_response_routes(async_client):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "global-limit-lock",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        limits[0].current_value = 1
        await session.commit()

    blocked_models = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert blocked_models.status_code == 429
    assert blocked_models.json()["error"]["code"] == "rate_limit_exceeded"

    blocked_responses = await async_client.post(
        "/v1/responses/compact",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": _TEST_MODELS[0], "instructions": "hi", "input": []},
    )
    assert blocked_responses.status_code == 429
    assert blocked_responses.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_update_key_reset_usage_requires_explicit_action(async_client):
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "reset-usage-explicit",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
            ],
        },
    )
    assert created.status_code == 200
    key_id = created.json()["id"]

    original_reset_at = utcnow() + timedelta(hours=4)
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        limits[0].current_value = 111
        limits[0].reset_at = original_reset_at
        await session.commit()

    untouched = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"name": "still-no-reset"},
    )
    assert untouched.status_code == 200

    explicit_reset = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"resetUsage": True},
    )
    assert explicit_reset.status_code == 200

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 0
        assert limits[0].reset_at > original_reset_at
