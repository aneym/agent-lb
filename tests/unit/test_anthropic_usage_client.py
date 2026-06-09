from __future__ import annotations

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
