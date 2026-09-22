from datetime import datetime, timedelta, timezone

import pytest

from app.core.clients.anthropic_resets import ResetStatus

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def status(**overrides):
    data = {
        "eligible": True,
        "at_limit": True,
        "exhausted": ["seven_day"],
        "next_grant_id": "launch",
        "grants": [
            {
                "id": "launch",
                "resets_left": 1,
                "usable_now": True,
                "clears": ["five_hour", "seven_day"],
                "ends_at": NOW + timedelta(days=1),
            }
        ],
    }
    data.update(overrides)
    return ResetStatus.model_validate(data)


def test_exhausted_account_can_use_provider_selected_grant():
    grant = status().usable_grant(NOW)
    assert grant is not None and grant.id == "launch"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"at_limit": False}, id="no-early-use"),
        pytest.param({"eligible": False}, id="provider-ineligible"),
        pytest.param({"exhausted": ["seven_day_opus"]}, id="grant-cannot-clear-blocker"),
        pytest.param({"cooldown_until": NOW + timedelta(minutes=1)}, id="provider-cooldown"),
    ],
)
def test_reset_does_not_spend_without_recoverable_exhaustion(overrides):
    assert status(**overrides).usable_grant(NOW) is None


def test_reset_does_not_use_expired_or_out_of_order_grant():
    inventory = status()
    inventory.grants[0].ends_at = NOW
    assert inventory.usable_grant(NOW) is None
    assert status().usable_grant(NOW, "different-grant") is None


def test_timezone_less_inventory_cannot_be_treated_as_fresh():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        status(grants=[{"id": "launch", "resets_left": 1, "ends_at": "2026-10-01T00:00:00"}])
