from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.accounts.api import build_availability_response
from app.modules.accounts.schemas import AccountSummary

pytestmark = pytest.mark.unit


def _summary(
    account_id: str,
    *,
    provider: str = "openai",
    status: str = "active",
    rate_limit_reset_at: datetime | None = None,
    reset_at_primary: datetime | None = None,
) -> AccountSummary:
    return AccountSummary(
        account_id=account_id,
        provider=provider,
        email=f"{account_id}@example.com",
        display_name=account_id,
        plan_type="pro",
        status=status,
        rate_limit_reset_at=rate_limit_reset_at,
        reset_at_primary=reset_at_primary,
    )


def test_availability_groups_by_provider_and_finds_earliest_recovery() -> None:
    soon = datetime(2026, 7, 10, 2, 41, tzinfo=timezone.utc)
    later = datetime(2026, 7, 10, 7, 25, tzinfo=timezone.utc)
    response = build_availability_response(
        [
            _summary("openai-ok"),
            _summary("openai-limited", status="rate_limited", rate_limit_reset_at=later),
            _summary("openai-limited-2", status="rate_limited", rate_limit_reset_at=soon),
            _summary("openai-reauth", status="reauth_required"),
            _summary("anthropic-ok", provider="anthropic"),
        ]
    )

    openai = response.providers["openai"]
    assert openai.total == 4
    assert openai.available == 1
    assert {entry.status for entry in openai.unavailable} == {"rate_limited", "reauth_required"}
    assert openai.earliest_recovery_at == soon

    anthropic = response.providers["anthropic"]
    assert anthropic.total == 1
    assert anthropic.available == 1
    assert anthropic.unavailable == []
    assert anthropic.earliest_recovery_at is None

    assert response.degradation.level in {"normal", "degraded", "critical"}
