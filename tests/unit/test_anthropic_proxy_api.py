from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy import anthropic_service as anthropic_service_module
from app.modules.proxy import api as proxy_api
from app.modules.proxy.anthropic_service import AnthropicCountTokensResult, AnthropicProxyError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_anthropic_stream_error_guard_releases_api_key_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation = ApiKeyUsageReservationData(
        reservation_id="res_stream_error",
        key_id="key_stream_error",
        model="claude-haiku-4-5",
    )
    released: list[ApiKeyUsageReservationData | None] = []

    async def fake_release_reservation(
        api_key_reservation: ApiKeyUsageReservationData | None,
    ) -> None:
        released.append(api_key_reservation)

    async def failing_body() -> AsyncIterator[bytes]:
        yield b"event: ping\n\n"
        raise AnthropicProxyError(
            503,
            "No available Anthropic accounts",
            code="no_available_anthropic_accounts",
        )

    monkeypatch.setattr(proxy_api, "_release_reservation", fake_release_reservation)

    chunks = [
        chunk
        async for chunk in proxy_api._anthropic_stream_error_guard(
            failing_body(),
            streaming=True,
            api_key_reservation=reservation,
        )
    ]

    assert chunks[0] == b"event: ping\n\n"
    assert b"event: error\n" in chunks[1]
    assert released == [reservation]


def test_is_fable_model_classification() -> None:
    from app.modules.proxy.anthropic_service import _is_fable_model

    assert _is_fable_model("claude-fable-5") is True
    assert _is_fable_model("CLAUDE-FABLE-5-20260101") is True
    assert _is_fable_model("claude-opus-4-8") is False
    assert _is_fable_model("claude-haiku-4-5") is False
    assert _is_fable_model("") is False
    assert _is_fable_model(None) is False


_COUNT_TOKENS_PAYLOAD = {
    "model": "claude-fable-5",
    "messages": [{"role": "user", "content": "hello"}],
    "thinking": {"type": "adaptive"},
}


def _guard_reservation_paths(monkeypatch: pytest.MonkeyPatch) -> tuple[list[object], list[object]]:
    """Fail loudly if count_tokens ever touches the reservation lifecycle."""
    enforced: list[object] = []
    released: list[object] = []

    async def fake_enforce_request_limits(*args: object, **kwargs: object) -> None:
        enforced.append((args, kwargs))
        return None

    async def fake_release_reservation(reservation: ApiKeyUsageReservationData | None) -> None:
        released.append(reservation)

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", fake_enforce_request_limits)
    monkeypatch.setattr(proxy_api, "_release_reservation", fake_release_reservation)
    return enforced, released


@pytest.mark.asyncio
async def test_v1_messages_count_tokens_forwards_upstream_response_without_reservation(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enforced, released = _guard_reservation_paths(monkeypatch)
    calls: dict[str, Any] = {}

    async def fake_count_tokens(
        self: object,
        body: Mapping[str, Any],
        inbound_headers: Mapping[str, str],
        *,
        model: str,
    ) -> AnthropicCountTokensResult:
        del self, inbound_headers
        calls["body"] = dict(body)
        calls["model"] = model
        return AnthropicCountTokensResult(
            status_code=200,
            body=b'{"input_tokens": 2095}',
            media_type="application/json",
        )

    monkeypatch.setattr(
        anthropic_service_module.AnthropicProxyService,
        "count_tokens",
        fake_count_tokens,
    )

    response = await async_client.post("/v1/messages/count_tokens", json=_COUNT_TOKENS_PAYLOAD)

    assert response.status_code != 405
    assert response.status_code == 200
    assert response.json() == {"input_tokens": 2095}
    assert calls["model"] == "claude-fable-5"
    assert calls["body"] == _COUNT_TOKENS_PAYLOAD
    assert enforced == []
    assert released == []


@pytest.mark.asyncio
async def test_v1_messages_count_tokens_passes_through_upstream_error_envelope(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream_error = b'{"type":"error","error":{"type":"invalid_request_error","message":"messages: field required"}}'

    async def fake_count_tokens(
        self: object,
        body: Mapping[str, Any],
        inbound_headers: Mapping[str, str],
        *,
        model: str,
    ) -> AnthropicCountTokensResult:
        del self, body, inbound_headers, model
        return AnthropicCountTokensResult(status_code=400, body=upstream_error, media_type="application/json")

    monkeypatch.setattr(
        anthropic_service_module.AnthropicProxyService,
        "count_tokens",
        fake_count_tokens,
    )

    response = await async_client.post("/v1/messages/count_tokens", json={"model": "claude-fable-5"})

    assert response.status_code == 400
    assert response.content == upstream_error


@pytest.mark.asyncio
async def test_v1_messages_count_tokens_selection_failure_returns_anthropic_envelope(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enforced, released = _guard_reservation_paths(monkeypatch)

    async def failing_count_tokens(
        self: object,
        body: Mapping[str, Any],
        inbound_headers: Mapping[str, str],
        *,
        model: str,
    ) -> AnthropicCountTokensResult:
        del self, body, inbound_headers, model
        raise AnthropicProxyError(
            503,
            "No available Anthropic accounts",
            code="no_available_anthropic_accounts",
        )

    monkeypatch.setattr(
        anthropic_service_module.AnthropicProxyService,
        "count_tokens",
        failing_count_tokens,
    )

    response = await async_client.post("/v1/messages/count_tokens", json=_COUNT_TOKENS_PAYLOAD)

    assert response.status_code == 503
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "no_available_anthropic_accounts",
            "message": "No available Anthropic accounts",
        },
    }
    assert enforced == []
    assert released == []


@pytest.mark.asyncio
async def test_v1_messages_count_tokens_requires_model(async_client) -> None:
    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "model is required",
        },
    }


# Header values below were captured from live Anthropic OAuth responses on
# 2026-07-01: a healthy subscription-covered 200 and a 200 served by billing
# extra-usage credits on an account whose 5h window sat at 100% utilization.
_HEALTHY_UNIFIED_HEADERS = {
    "anthropic-ratelimit-unified-status": "allowed",
    "anthropic-ratelimit-unified-5h-status": "allowed",
    "anthropic-ratelimit-unified-7d-status": "allowed",
    "anthropic-ratelimit-unified-reset": "1782959400",
    "anthropic-ratelimit-unified-overage-status": "rejected",
    "anthropic-ratelimit-unified-overage-disabled-reason": "org_level_disabled",
}
_OVERAGE_BILLING_UNIFIED_HEADERS = {
    "anthropic-ratelimit-unified-status": "rejected",
    "anthropic-ratelimit-unified-5h-status": "rejected",
    "anthropic-ratelimit-unified-5h-utilization": "1.0",
    "anthropic-ratelimit-unified-7d-status": "allowed",
    "anthropic-ratelimit-unified-overage-status": "allowed",
    "anthropic-ratelimit-unified-overage-in-use": "true",
    "anthropic-ratelimit-unified-reset": "1782958200",
    "anthropic-ratelimit-unified-overage-reset": "1785542400",
}


def test_unified_tripwire_ignores_healthy_subscription_response() -> None:
    assert anthropic_service_module._extra_usage_tripwire_error(_HEALTHY_UNIFIED_HEADERS) is None


def test_unified_tripwire_detects_overage_billing_response() -> None:
    error = anthropic_service_module._extra_usage_tripwire_error(_OVERAGE_BILLING_UNIFIED_HEADERS)

    assert error is not None
    assert error["resets_at"] == 1782958200


def test_unified_tripwire_detects_allowed_warning_as_healthy() -> None:
    headers = dict(_HEALTHY_UNIFIED_HEADERS)
    headers["anthropic-ratelimit-unified-status"] = "allowed_warning"

    assert anthropic_service_module._extra_usage_tripwire_error(headers) is None


def test_unified_tripwire_fails_closed_on_unknown_status() -> None:
    headers = dict(_HEALTHY_UNIFIED_HEADERS)
    headers["anthropic-ratelimit-unified-status"] = "exhausted"

    assert anthropic_service_module._extra_usage_tripwire_error(headers) is not None


def test_unified_tripwire_ignores_responses_without_unified_headers() -> None:
    assert anthropic_service_module._extra_usage_tripwire_error({}) is None
    assert anthropic_service_module._extra_usage_tripwire_error({"content-type": "text/event-stream"}) is None


def test_pool_wait_delay_clamps_to_poll_bounds_and_remaining(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_service_module, "_POOL_WAIT_JITTER", lambda: 0.0)

    # Reset far away: bounded by the max poll interval so early frees are seen.
    assert (
        anthropic_service_module._pool_wait_delay_seconds(10_000, now=0.0, remaining=100_000.0)
        == anthropic_service_module._POOL_WAIT_MAX_POLL_SECONDS
    )
    # Reset already passed: re-poll on the min cadence instead of spinning.
    assert (
        anthropic_service_module._pool_wait_delay_seconds(10, now=50.0, remaining=100_000.0)
        == anthropic_service_module._POOL_WAIT_MIN_POLL_SECONDS
    )
    # Never sleeps past the wait cap.
    assert anthropic_service_module._pool_wait_delay_seconds(10_000, now=0.0, remaining=42.0) == 42.0


def test_pool_wait_should_hold_requires_reset_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_service_module, "_POOL_WAIT_CLOCK", lambda: 100.0)
    error_with_reset = AnthropicProxyError(429, "cooling", retry_at=1_000)
    error_without_reset = AnthropicProxyError(503, "no accounts")

    assert anthropic_service_module._pool_wait_should_hold(error_with_reset, wait_enabled=True, deadline=200.0) is True
    assert (
        anthropic_service_module._pool_wait_should_hold(error_without_reset, wait_enabled=True, deadline=200.0) is False
    )
    assert (
        anthropic_service_module._pool_wait_should_hold(error_with_reset, wait_enabled=False, deadline=200.0) is False
    )
    assert anthropic_service_module._pool_wait_should_hold(error_with_reset, wait_enabled=True, deadline=50.0) is False


def test_overloaded_backoff_progression_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_service_module, "_OVERLOADED_RETRY_JITTER", lambda: 0.0)

    delays = [anthropic_service_module._overloaded_backoff_seconds(attempt) for attempt in range(5)]

    assert delays == [0.25, 0.5, 1.0, 2.0, 2.0]
