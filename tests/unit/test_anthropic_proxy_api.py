from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy import api as proxy_api
from app.modules.proxy.anthropic_service import AnthropicProxyError

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
