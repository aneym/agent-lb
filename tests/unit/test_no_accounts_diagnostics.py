from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.balancer import AccountState, select_account
from app.core.errors import openai_error
from app.db.models import AccountStatus
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.selection_diagnostics import (
    enrich_selection_error,
    retry_after_seconds_from_error_detail,
    selection_error_extras,
)
from app.modules.usage.identity_mismatch import (
    ESCALATION_THRESHOLD,
    clear_identity_mismatch,
    get_identity_mismatch,
    record_identity_mismatch,
    reset_for_tests,
)

pytestmark = pytest.mark.unit


def test_select_account_captures_rate_limited_exclusions_and_earliest_retry() -> None:
    now = 1_784_000_000.0
    earliest_reset = now + 120
    later_reset = now + 300
    states = [
        AccountState(
            "rate-limited-later",
            AccountStatus.RATE_LIMITED,
            reset_at=later_reset,
            deactivation_reason="upstream 429",
            plan_type="pro",
        ),
        AccountState(
            "rate-limited-earlier",
            AccountStatus.RATE_LIMITED,
            reset_at=earliest_reset,
            plan_type="plus",
        ),
    ]

    result = select_account(states, now=now)

    assert result.account is None
    assert result.retry_at == datetime.fromtimestamp(earliest_reset, tz=timezone.utc)
    assert [excluded.account_id for excluded in result.excluded_accounts] == [
        "rate-limited-later",
        "rate-limited-earlier",
    ]
    assert [excluded.status for excluded in result.excluded_accounts] == [
        "rate_limited",
        "rate_limited",
    ]
    assert result.excluded_accounts[0].reset_at == datetime.fromtimestamp(later_reset, tz=timezone.utc)
    assert result.excluded_accounts[0].cooldown_until is None
    assert result.excluded_accounts[0].deactivation_reason == "upstream 429"
    assert result.excluded_accounts[0].plan_type == "pro"


def _failed_selection(now: float) -> AccountSelection:
    core = select_account(
        [
            AccountState("a", AccountStatus.RATE_LIMITED, reset_at=now + 600, plan_type="pro"),
            AccountState("b", AccountStatus.REAUTH_REQUIRED, plan_type="pro"),
        ],
        now=now,
    )
    return AccountSelection(
        account=None,
        error_message=core.error_message,
        error_code="no_accounts",
        excluded_accounts=list(core.excluded_accounts),
        retry_at=core.retry_at,
    )


def test_selection_error_extras_carry_diagnostics_and_reset_hint() -> None:
    now = 1_784_000_000.0
    selection = _failed_selection(now)

    envelope = enrich_selection_error(
        openai_error("no_accounts", "No available accounts"),
        selection,
        requested_model="gpt-5.6-sol",
    )
    detail = envelope["error"]

    assert detail["code"] == "no_accounts"
    assert detail["resets_at"] == int(now + 600)
    diagnostics = detail["diagnostics"]
    assert diagnostics["degraded"] is True
    assert diagnostics["requested_model"] == "gpt-5.6-sol"
    assert {entry["status"] for entry in diagnostics["accounts"]} == {
        "rate_limited",
        "reauth_required",
    }
    assert diagnostics["earliest_recovery_at"] == datetime.fromtimestamp(now + 600, tz=timezone.utc).isoformat()
    # Retry-After derives from resets_in_seconds at the response layer.
    assert retry_after_seconds_from_error_detail(detail) >= 0


def test_selection_error_extras_empty_without_diagnostics() -> None:
    bare = AccountSelection(account=None, error_message="No available accounts")
    assert selection_error_extras(bare) == {}
    assert retry_after_seconds_from_error_detail({"message": "x"}) is None


def test_identity_mismatch_escalates_after_threshold_and_resets_on_success() -> None:
    reset_for_tests()
    account_id = "acct-mismatch"
    try:
        for _ in range(ESCALATION_THRESHOLD - 1):
            record_identity_mismatch(account_id, stored_plan_type="pro", payload_plan_type="free")
        assert get_identity_mismatch(account_id) is None  # below threshold

        record_identity_mismatch(account_id, stored_plan_type="pro", payload_plan_type="free")
        escalated = get_identity_mismatch(account_id)
        assert escalated is not None
        assert escalated["count"] == ESCALATION_THRESHOLD
        assert escalated["stored_plan_type"] == "pro"
        assert escalated["payload_plan_type"] == "free"

        clear_identity_mismatch(account_id)
        assert get_identity_mismatch(account_id) is None
    finally:
        reset_for_tests()
