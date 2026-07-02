from __future__ import annotations

from datetime import datetime

import pytest

from app.core.clients.anthropic_usage import AnthropicOAuthUsagePayload, _usage_payload_from_anthropic

pytestmark = pytest.mark.unit


def test_anthropic_usage_maps_top_model_session_and_week_windows_only() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {
                "utilization": 100.0,
                "resets_at": "2026-06-09T19:50:00.000000+00:00",
            },
            "seven_day": {
                "utilization": 21.0,
                "resets_at": "2026-06-14T21:00:00.000000+00:00",
            },
            "seven_day_sonnet": {
                "utilization": 1.0,
                "resets_at": "2026-06-14T21:00:00.000000+00:00",
            },
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.plan_type == "claude"
    assert usage.rate_limit is not None
    assert usage.rate_limit.primary_window is not None
    assert usage.rate_limit.primary_window.used_percent == 100.0
    assert usage.rate_limit.primary_window.limit_window_seconds == 5 * 60 * 60
    assert usage.rate_limit.primary_window.reset_at == 1781034600
    assert usage.rate_limit.secondary_window is not None
    assert usage.rate_limit.secondary_window.used_percent == 21.0
    assert usage.rate_limit.secondary_window.limit_window_seconds == 7 * 24 * 60 * 60
    assert usage.rate_limit.secondary_window.reset_at == 1781470800
    assert usage.credits is None


def test_anthropic_usage_maps_enabled_extra_usage_to_credits() -> None:
    # Shape observed live on /api/oauth/usage (2026-07-01): monthly_limit and
    # used_credits arrive in currency minor units scaled by decimal_places.
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 100.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "seven_day": {"utilization": 20.0, "resets_at": "2026-07-05T21:00:00+00:00"},
            "extra_usage": {
                "is_enabled": True,
                "monthly_limit": 25000,
                "used_credits": 6340.0,
                "utilization": 25.36,
                "currency": "USD",
                "decimal_places": 2,
                "disabled_reason": None,
            },
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.credits is not None
    assert usage.credits.has_credits is True
    assert usage.credits.unlimited is False
    assert usage.credits.balance is not None
    assert float(usage.credits.balance) == pytest.approx(186.60)


def test_anthropic_usage_maps_disabled_extra_usage_without_fabricated_balance() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 10.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "extra_usage": {
                "is_enabled": False,
                "monthly_limit": None,
                "used_credits": None,
                "utilization": None,
                "currency": None,
                "decimal_places": None,
            },
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.credits is not None
    assert usage.credits.has_credits is False
    assert usage.credits.unlimited is False
    assert usage.credits.balance is None


def test_anthropic_usage_extracts_fable_scoped_weekly_limit() -> None:
    # Real captured api/oauth/usage payload shape (2026-07-02).
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 10.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "seven_day": {"utilization": 62.0, "resets_at": "2026-07-05T21:00:00.091084+00:00"},
            "limits": [
                {
                    "kind": "session",
                    "group": "session",
                    "percent": 52,
                    "severity": "normal",
                    "resets_at": "2026-07-02T18:09:59.091065+00:00",
                    "scope": None,
                    "is_active": False,
                },
                {
                    "kind": "weekly_all",
                    "group": "weekly",
                    "percent": 62,
                    "severity": "normal",
                    "resets_at": "2026-07-05T21:00:00.091084+00:00",
                    "scope": None,
                    "is_active": False,
                },
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 81,
                    "severity": "warning",
                    "resets_at": "2026-07-05T21:00:00.091366+00:00",
                    "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
                    "is_active": True,
                },
            ],
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.fable_scoped_weekly is not None
    assert usage.fable_scoped_weekly.used_percent == 81.0
    assert usage.fable_scoped_weekly.limit_window_seconds == 7 * 24 * 60 * 60
    assert usage.fable_scoped_weekly.reset_at == int(
        datetime.fromisoformat("2026-07-05T21:00:00.091366+00:00").timestamp()
    )


def test_anthropic_usage_absent_limits_yields_no_fable_scoped_weekly() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 10.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "seven_day": {"utilization": 20.0, "resets_at": "2026-07-05T21:00:00+00:00"},
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.fable_scoped_weekly is None


def test_anthropic_usage_ignores_weekly_scoped_entry_for_non_fable_model() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "seven_day": {"utilization": 20.0, "resets_at": "2026-07-05T21:00:00+00:00"},
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 90,
                    "severity": "warning",
                    "resets_at": "2026-07-05T21:00:00+00:00",
                    "scope": {"model": {"id": None, "display_name": "Sonnet"}, "surface": None},
                    "is_active": True,
                },
            ],
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.fable_scoped_weekly is None


def test_anthropic_usage_matches_fable_scope_case_insensitively() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "seven_day": {"utilization": 20.0, "resets_at": "2026-07-05T21:00:00+00:00"},
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 45,
                    "severity": "normal",
                    "resets_at": "2026-07-05T21:00:00+00:00",
                    "scope": {"model": {"id": None, "display_name": "fABLE"}, "surface": None},
                    "is_active": True,
                },
            ],
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.fable_scoped_weekly is not None
    assert usage.fable_scoped_weekly.used_percent == 45.0


def test_anthropic_usage_extra_usage_balance_never_goes_negative() -> None:
    payload = AnthropicOAuthUsagePayload.model_validate(
        {
            "five_hour": {"utilization": 100.0, "resets_at": "2026-07-02T02:10:00+00:00"},
            "extra_usage": {
                "is_enabled": True,
                "monthly_limit": 1000,
                "used_credits": 1500.0,
                "utilization": 150.0,
                "currency": "USD",
                "decimal_places": 2,
            },
        }
    )

    usage = _usage_payload_from_anthropic(payload)

    assert usage.credits is not None
    assert usage.credits.has_credits is True
    assert usage.credits.balance is not None
    assert float(usage.credits.balance) == 0.0
