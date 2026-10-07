"""COST_USD caps meter Claude traffic at list price (bops S16 spend floor, 2026-10-07).

Before the fix, cap accounting priced every Claude request at $0, so a capped
caller never tripped. The upstream is faked at the network edge only; the API
key, its limits, the reservation and its settlement run through the real HTTP
route and database.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from typing import Any

import pytest

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.pricing import DEFAULT_PRICING_MODELS
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, LimitType
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeyRequestUsageBudget,
    ApiKeysService,
    LimitRuleInput,
)

pytestmark = pytest.mark.integration

PRICED_MODEL = "claude-sonnet-4-5-20250929"
UNPRICED_MODEL = "claude-unpriced-test-0"
INPUT_TOKENS = 1_000
CACHE_READ_TOKENS = 4_000
CACHE_WRITE_5M_TOKENS = 1_500
CACHE_WRITE_1H_TOKENS = 500
OUTPUT_TOKENS = 500
CAP_MICRODOLLARS = 30_000


def _sse(model: str) -> bytes:
    start = {
        "type": "message_start",
        "message": {
            "id": "msg_cap",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "usage": {
                "input_tokens": INPUT_TOKENS,
                "cache_creation_input_tokens": CACHE_WRITE_5M_TOKENS + CACHE_WRITE_1H_TOKENS,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": CACHE_WRITE_5M_TOKENS,
                    "ephemeral_1h_input_tokens": CACHE_WRITE_1H_TOKENS,
                },
                "cache_read_input_tokens": CACHE_READ_TOKENS,
            },
        },
    }
    delta = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": OUTPUT_TOKENS}}
    return (
        f"event: message_start\ndata: {json.dumps(start)}\n\n"
        f"event: message_delta\ndata: {json.dumps(delta)}\n\n"
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    ).encode()


class _Content:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_any(self):
        yield self._body

    async def iter_chunked(self, _size: int):
        yield self._body


class _Response:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self.headers: dict[str, str] = {}
        self.content = _Content(body)
        self._body = body

    async def read(self) -> bytes:
        return self._body


class _ResponseContext:
    def __init__(self, response: _Response) -> None:
        self._response = response

    async def __aenter__(self) -> _Response:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


async def _insert_anthropic_account() -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="anthropic-cap-account",
                provider="anthropic",
                chatgpt_account_id="anthropic-cap-account",
                email="cap@example.com",
                plan_type="max",
                access_token_encrypted=encryptor.encrypt("placeholder-access"),
                refresh_token_encrypted=encryptor.encrypt("placeholder-refresh"),
                id_token_encrypted=None,
                last_refresh=utcnow() + timedelta(days=1),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        await session.commit()


def _list_price_microdollars() -> int:
    # Computed from the price table and the token counts, independently of the cap code.
    price = DEFAULT_PRICING_MODELS["claude-sonnet-4-5"]
    usd = (
        INPUT_TOKENS * price.input_per_1m
        + CACHE_WRITE_5M_TOKENS * price.cache_creation_5m_input_per_1m
        + CACHE_WRITE_1H_TOKENS * price.cache_creation_1h_input_per_1m
        + CACHE_READ_TOKENS * price.cache_read_input_per_1m
        + OUTPUT_TOKENS * price.output_per_1m
    ) / 1_000_000
    return round(usd * 1_000_000)


async def _create_key(async_client, name: str, limits: list[dict[str, Any]]) -> tuple[str, str]:
    created = await async_client.post("/api/api-keys/", json={"name": name, "limits": limits})
    assert created.status_code == 200, created.text
    body = created.json()
    return body["id"], body["key"]


async def _send(async_client, key: str | None, model: str):
    headers = {"anthropic-version": "2023-06-01", "user-agent": "agent-lb-cost-cap-test"}
    if key is not None:
        headers["authorization"] = f"Bearer {key}"
    payload = {"model": model, "max_tokens": 16, "stream": True, "messages": [{"role": "user", "content": "hi"}]}
    async with async_client.stream("POST", "/v1/messages", json=payload, headers=headers) as response:
        body = await response.aread()
    return response.status_code, body


@pytest.mark.asyncio
async def test_cost_cap_trips_on_claude_list_price_refuses_unpriced_and_leaves_uncapped(async_client, monkeypatch):
    await _insert_anthropic_account()
    upstream_models: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        upstream_models.append(json_body["model"])
        return _ResponseContext(_Response(_sse(json_body["model"])))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService, "_open_upstream_response", fake_open_upstream_response
    )

    per_request = _list_price_microdollars()
    assert per_request > 0
    expected_trip = math.ceil(CAP_MICRODOLLARS / per_request) + 1

    capped_id, capped_key = await _create_key(
        async_client,
        "cost-capped",
        [{"limitType": "cost_usd", "limitWindow": "daily", "maxValue": CAP_MICRODOLLARS}],
    )
    statuses: list[int] = []
    for _ in range(expected_trip + 1):
        status, body = await _send(async_client, capped_key, PRICED_MODEL)
        statuses.append(status)
        if status != 200:
            error = json.loads(body)["error"]
            assert error["type"] == "rate_limit_error"
            assert "cost_usd daily limit exceeded" in error["message"]
            break
    assert statuses == [200] * (expected_trip - 1) + [429]
    assert len(upstream_models) == expected_trip - 1

    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(capped_id)
    cost_limit = next(limit for limit in limits if limit.limit_type == LimitType.COST_USD)
    assert cost_limit.current_value == per_request * (expected_trip - 1)

    _, unpriced_key = await _create_key(
        async_client,
        "cost-capped-unpriced",
        [{"limitType": "cost_usd", "limitWindow": "daily", "maxValue": CAP_MICRODOLLARS}],
    )
    upstream_before = len(upstream_models)
    status, body = await _send(async_client, unpriced_key, UNPRICED_MODEL)
    assert status == 403
    error = json.loads(body)["error"]
    assert error["type"] == "permission_error"
    assert error["code"] == "model_unpriced_under_cost_cap"
    assert UNPRICED_MODEL in error["message"]
    assert "cost_usd daily cap" in error["message"]
    assert len(upstream_models) == upstream_before

    _, token_only_key = await _create_key(
        async_client,
        "token-capped-unpriced",
        [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1_000_000}],
    )
    status, body = await _send(async_client, token_only_key, UNPRICED_MODEL)
    assert status == 200
    assert body == _sse(UNPRICED_MODEL)

    _, uncapped_key = await _create_key(async_client, "uncapped", [])
    for key, model in [(uncapped_key, PRICED_MODEL)] * (expected_trip + 1) + [
        (uncapped_key, UNPRICED_MODEL),
        (None, PRICED_MODEL),
        (None, UNPRICED_MODEL),
    ]:
        status, body = await _send(async_client, key, model)
        assert status == 200
        assert body == _sse(model)


@pytest.mark.asyncio
async def test_cost_cap_keeps_reservation_for_usage_it_cannot_price(db_setup):
    """A model-less request that consumed tokens is billed its reservation, never $0; one that consumed none is not."""
    del db_setup
    budget = ApiKeyRequestUsageBudget(input_tokens=8_192, output_tokens=2_048)
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name="unpriced-usage",
                allowed_models=None,
                expires_at=None,
                limits=[LimitRuleInput(limit_type="cost_usd", limit_window="daily", max_value=10_000_000)],
            )
        )
        consumed = await service.enforce_limits_for_request(created.id, request_model=None, request_usage_budget=budget)
        idle = await service.enforce_limits_for_request(created.id, request_model=None, request_usage_budget=budget)
        await service.finalize_usage_reservation(consumed.reservation_id, model="", input_tokens=100, output_tokens=100)
        await service.finalize_usage_reservation(idle.reservation_id, model="", input_tokens=0, output_tokens=0)

    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(created.id)
    cost_limit = next(limit for limit in limits if limit.limit_type == LimitType.COST_USD)
    # The unknown-model reserve for 8,192 in + 2,048 out is ceil(2 USD * 10,240 / 16,384) = 1.25 USD.
    assert cost_limit.current_value == 1_250_000
