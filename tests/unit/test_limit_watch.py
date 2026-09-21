from __future__ import annotations

import contextlib
import io
import runpy
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
LIMIT_WATCH = ROOT / "config" / "coding-agents" / "limit-watch"


def account(provider="anthropic", **changes):
    value = {
        "accountId": "12345678-long-account-id",
        "provider": provider,
        "status": "active",
        "usage": {"primaryRemainingPercent": 55, "secondaryRemainingPercent": 65},
        "additionalQuotas": [],
    }
    value.update(changes)
    return value


def snapshot(module, accounts):
    output = io.StringIO()
    args = SimpleNamespace(reserve=20.0, interval=300, dry_run=True)
    with patch.dict(module["run_once"].__globals__, {
        "fetch_accounts": lambda: {"accounts": accounts}, "file_ask": lambda *args: None,
    }), \
            contextlib.redirect_stdout(output):
        module["run_once"](args)
    import json
    return json.loads(output.getvalue())


def test_active_anthropic_with_numeric_windows_above_reserve_is_usable():
    module = runpy.run_path(str(LIMIT_WATCH))
    result = snapshot(module, [account()])
    assert result["providers"]["anthropic"]["usable_count"] == 1


def test_anthropic_window_at_reserve_is_unusable_with_reason():
    module = runpy.run_path(str(LIMIT_WATCH))
    result = snapshot(module, [account(usage={"primaryRemainingPercent": 20, "secondaryRemainingPercent": 65})])
    assert result["providers"]["anthropic"]["usable_count"] == 0
    assert result["providers"]["anthropic"]["reasons"] == [
        {"account_id": "12345678", "reasons": ["primary remaining <= reserve"]}
    ]


def test_anthropic_null_window_is_unusable_with_reason():
    module = runpy.run_path(str(LIMIT_WATCH))
    result = snapshot(module, [account(usage={"primaryRemainingPercent": None, "secondaryRemainingPercent": 65})])
    assert result["providers"]["anthropic"]["usable_count"] == 0
    assert result["providers"]["anthropic"]["reasons"] == [
        {"account_id": "12345678", "reasons": ["primary remaining missing or non-numeric"]}
    ]


def test_openai_null_primary_uses_weekly_alone():
    module = runpy.run_path(str(LIMIT_WATCH))
    result = snapshot(module, [account("openai", usage={
        "primaryRemainingPercent": None, "secondaryRemainingPercent": 21,
    })])
    assert result["providers"]["openai"]["usable_count"] == 1


def test_glm_primary_only_account_stays_usable():
    module = runpy.run_path(str(LIMIT_WATCH))
    result = snapshot(module, [account("glm", usage={
        "primaryRemainingPercent": 100.0, "secondaryRemainingPercent": None,
    })])
    assert result["providers"]["glm"]["usable_count"] == 1


def test_openai_weekly_at_reserve_is_unusable():
    module = runpy.run_path(str(LIMIT_WATCH))
    result = snapshot(module, [account("openai", usage={
        "primaryRemainingPercent": None, "secondaryRemainingPercent": 20,
    })])
    assert result["providers"]["openai"]["usable_count"] == 0


def test_fable_scoped_weekly_reserve_and_staleness_exclude_fable_count():
    module = runpy.run_path(str(LIMIT_WATCH))
    at_reserve = account(fableEligible=True, additionalQuotas=[{
        "quotaKey": "anthropic_fable_scoped_weekly",
        "primaryWindow": {"usedPercent": 80},
    }])
    stale = account(accountId="abcdefgh-more", fableEligible=True, fableScopedWeekly={
        "fresh": False,
    }, additionalQuotas=[{
        "quotaKey": "anthropic_fable_scoped_weekly",
        "primaryWindow": {"usedPercent": 50},
    }])
    old_record = account(accountId="ijklmnop-more", fableEligible=True, fableScopedWeekly={
        "fresh": True,
        "recordedAt": (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),
    }, additionalQuotas=[{
        "quotaKey": "anthropic_fable_scoped_weekly",
        "primaryWindow": {"usedPercent": 50},
    }])
    result = snapshot(module, [at_reserve, stale, old_record])
    assert result["providers"]["anthropic"]["usable_count"] == 3
    assert result["providers"]["anthropic"]["freshness"] == "unknown"
    assert result["fable_eligible_usable"] == 0
    assert result["fable_reasons"] == [
        {"account_id": "12345678", "reasons": ["fable scoped weekly remaining <= reserve"]},
        {"account_id": "abcdefgh", "reasons": ["fable window stale"]},
        {"account_id": "ijklmnop", "reasons": ["fable window stale"]},
    ]


def test_healthy_openai_account_with_days_old_last_refresh_stays_usable():
    module = runpy.run_path(str(LIMIT_WATCH))
    result = snapshot(module, [account("openai", lastRefreshAt=(
        datetime.now(timezone.utc) - timedelta(days=4)
    ).isoformat(), usage={"primaryRemainingPercent": None, "secondaryRemainingPercent": 65})])
    assert result["providers"]["openai"]["usable_count"] == 1
    assert result["providers"]["openai"]["freshness"] == "unknown"


def test_inactive_and_future_rate_limited_accounts_are_unusable():
    module = runpy.run_path(str(LIMIT_WATCH))
    reset = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    result = snapshot(module, [
        account(accountId="inactive-one", status="quota_exceeded"),
        account(accountId="limited-one", rateLimitResetAt=reset),
    ])
    assert result["providers"]["anthropic"]["usable_count"] == 0
    assert result["providers"]["anthropic"]["reasons"] == [
        {"account_id": "inactive", "reasons": ["status is not active"]},
        {"account_id": "limited-", "reasons": [f"rate limited until {reset}"]},
    ]


def test_snapshot_preserves_guard_keys_and_reports_minimum():
    module = runpy.run_path(str(LIMIT_WATCH))
    result = snapshot(module, [
        account(accountId="aaaaaaaa-one", usage={"primaryRemainingPercent": 25, "secondaryRemainingPercent": 70}),
        account(accountId="bbbbbbbb-two", usage={"primaryRemainingPercent": 30, "secondaryRemainingPercent": 45}),
    ])
    assert result["reachable"] is True
    assert result["polled_at"]
    assert result["providers"]["anthropic"]["usable_count"] == 2
    assert result["fable_eligible_usable"] == 0
    assert result["providers"]["anthropic"]["min_remaining_percent"] == 25


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite


if __name__ == "__main__":
    unittest.main()
