from unittest.mock import AsyncMock

import pytest

from app.core.clients import anthropic_resets
from app.core.clients.rate_limit_resets import ResetCreditsError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_reset_uses_account_profile_org_and_durable_request_id(monkeypatch):
    request = AsyncMock(
        side_effect=[
            {"organization": {"uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}},
            {"result": "reset", "cleared": ["five_hour", "seven_day"]},
        ]
    )
    monkeypatch.setattr(anthropic_resets, "_request", request)
    result = await anthropic_resets.redeem(
        access_token="test",
        grant_id="launch",
        request_id="11111111-2222-3333-4444-555555555555",
    )
    assert result.result == "reset"
    assert request.await_args.args[:2] == (
        "POST",
        "api/organizations/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/reset_rate_limits",
    )
    assert request.await_args.kwargs["body"] == {
        "program": "cedar_ember",
        "grant_id": "launch",
        "request_id": "11111111-2222-3333-4444-555555555555",
    }


@pytest.mark.asyncio
async def test_unknown_reset_response_cannot_be_reported_as_success(monkeypatch):
    request = AsyncMock(
        side_effect=[
            {"organization": {"uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}},
            {"success": True},
        ]
    )
    monkeypatch.setattr(anthropic_resets, "_request", request)
    with pytest.raises(ResetCreditsError, match="outcome is unknown"):
        await anthropic_resets.redeem(
            access_token="test",
            grant_id="launch",
            request_id="11111111-2222-3333-4444-555555555555",
        )
    assert request.await_count == 2


@pytest.mark.asyncio
async def test_missing_profile_org_prevents_reset_post(monkeypatch):
    request = AsyncMock(return_value={"organization": {}})
    monkeypatch.setattr(anthropic_resets, "_request", request)
    with pytest.raises(ResetCreditsError, match="organization"):
        await anthropic_resets.redeem(
            access_token="test",
            grant_id="launch",
            request_id="11111111-2222-3333-4444-555555555555",
        )
    assert request.await_count == 1
