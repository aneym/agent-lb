from __future__ import annotations

import json
from datetime import timedelta, timezone

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.openai.models import CompactResponsePayload, OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository
from tests.integration.proxy_compact_support import (
    _make_auth_json,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_proxy_compact_reaches_upstream_despite_exhausted_additional_quota(async_client, monkeypatch):
    email = "compact-gated@example.com"
    raw_account_id = "acc_compact_gated"
    auth_json = _make_auth_json(raw_account_id, email, plan_type="pro")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        additional_repo = AdditionalUsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=25.0,
            window="primary",
            reset_at=now_epoch + 300,
            window_minutes=5,
            recorded_at=now,
        )
        await additional_repo.add_entry(
            account_id=expected_account_id,
            limit_name="codex_other",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=100.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
            recorded_at=now,
        )

    attempted_accounts: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        attempted_accounts.append(account_id)
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    payload = {"model": "gpt-5.3-codex-spark", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert attempted_accounts == [raw_account_id]


@pytest.mark.asyncio
async def test_proxy_compact_headers_include_monthly_only_credits(async_client, monkeypatch):
    email = "compact-monthly@example.com"
    raw_account_id = "acc_compact_monthly"
    auth_json = _make_auth_json(raw_account_id, email, plan_type="free")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)

    async def fake_compact(payload, headers, access_token, account_id):
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=40.0,
            window="monthly",
            reset_at=1735862400,
            window_minutes=43200,
            recorded_at=utcnow(),
            credits_has=True,
            credits_unlimited=False,
            credits_balance=8.75,
        )

    await get_rate_limit_headers_cache().invalidate()

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.headers.get("x-codex-monthly-used-percent") == "40.0"
    assert response.headers.get("x-codex-monthly-window-minutes") == "43200"
    assert response.headers.get("x-codex-monthly-reset-at") == "1735862400"
    assert response.headers.get("x-codex-credits-has-credits") == "true"
    assert response.headers.get("x-codex-credits-unlimited") == "false"
    assert response.headers.get("x-codex-credits-balance") == "8.75"


@pytest.mark.asyncio
async def test_proxy_compact_headers_normalize_weekly_only_with_stale_secondary(async_client, monkeypatch):
    email = "compact-weekly@example.com"
    raw_account_id = "acc_compact_weekly"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    now = utcnow()

    async def fake_compact(payload, headers, access_token, account_id):
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=15.0,
            window="secondary",
            reset_at=1735689600,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=80.0,
            window="primary",
            reset_at=1735862400,
            window_minutes=10080,
            recorded_at=now,
        )

    await get_rate_limit_headers_cache().invalidate()

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.headers.get("x-codex-primary-used-percent") is None
    assert response.headers.get("x-codex-secondary-used-percent") == "80.0"
    assert response.headers.get("x-codex-secondary-window-minutes") == "10080"
    assert response.headers.get("x-codex-secondary-reset-at") == "1735862400"


@pytest.mark.asyncio
async def test_proxy_compact_usage_limit_marks_account(async_client, monkeypatch):
    email = "limit@example.com"
    raw_account_id = "acc_limit"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)

    async def fake_compact(payload, headers, access_token, account_id):
        raise ProxyResponseError(
            429,
            {
                "error": {
                    "type": "usage_limit_reached",
                    "message": "limit reached",
                    "plan_type": "plus",
                    "resets_at": 1767612327,
                }
            },
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "usage_limit_reached"

    async with SessionLocal() as session:
        account = await session.get(Account, expected_account_id)
        assert account is not None
        assert account.status == AccountStatus.RATE_LIMITED
