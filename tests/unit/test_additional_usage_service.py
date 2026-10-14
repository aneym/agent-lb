from __future__ import annotations

import pytest

from app.db.models import AdditionalUsageHistory
from app.modules.usage.builders import (
    AdditionalQuotaSummary,
    AdditionalWindowSummary,
    build_additional_usage_summary,
)

pytestmark = pytest.mark.unit


def _make_entry(
    *,
    account_id: str,
    limit_name: str,
    metered_feature: str = "codex_other",
    window: str = "primary",
    used_percent: float = 0.0,
    reset_at: int | None = None,
    window_minutes: int | None = None,
) -> AdditionalUsageHistory:
    return AdditionalUsageHistory(
        account_id=account_id,
        quota_key=limit_name,
        limit_name=limit_name,
        metered_feature=metered_feature,
        window=window,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes,
    )


def test_build_additional_usage_summary_aggregates_accounts():

    data: dict[str, dict[str, dict[str, AdditionalUsageHistory]]] = {
        "codex_other": {
            "primary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="codex_other",
                    used_percent=30.0,
                    reset_at=1000,
                    window_minutes=60,
                ),
                "acc2": _make_entry(
                    account_id="acc2",
                    limit_name="codex_other",
                    used_percent=50.0,
                    reset_at=2000,
                    window_minutes=60,
                ),
                "acc3": _make_entry(
                    account_id="acc3",
                    limit_name="codex_other",
                    used_percent=70.0,
                    reset_at=1500,
                    window_minutes=120,
                ),
            },
            "secondary": {},
        },
    }

    result = build_additional_usage_summary(data)

    assert len(result) == 1
    summary = result[0]
    assert isinstance(summary, AdditionalQuotaSummary)
    assert summary.limit_name == "codex_other"
    assert summary.metered_feature == "codex_other"

    assert summary.primary_window is not None
    assert isinstance(summary.primary_window, AdditionalWindowSummary)
    assert summary.primary_window.used_percent == pytest.approx(50.0)
    assert summary.primary_window.reset_at == 1000
    assert summary.primary_window.window_minutes == 120

    assert summary.secondary_window is None


def test_build_additional_usage_summary_with_secondary_window():

    data: dict[str, dict[str, dict[str, AdditionalUsageHistory]]] = {
        "codex_other": {
            "primary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="codex_other",
                    used_percent=40.0,
                    reset_at=1000,
                    window_minutes=60,
                ),
            },
            "secondary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="codex_other",
                    window="secondary",
                    used_percent=20.0,
                    reset_at=5000,
                    window_minutes=1440,
                ),
            },
        },
    }

    result = build_additional_usage_summary(data)

    assert len(result) == 1
    summary = result[0]
    assert summary.primary_window is not None
    assert summary.primary_window.used_percent == pytest.approx(40.0)
    assert summary.secondary_window is not None
    assert summary.secondary_window.used_percent == pytest.approx(20.0)
    assert summary.secondary_window.reset_at == 5000
    assert summary.secondary_window.window_minutes == 1440


def test_build_additional_usage_summary_missing_account_data():

    data: dict[str, dict[str, dict[str, AdditionalUsageHistory]]] = {
        "codex_other": {
            "primary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="codex_other",
                    used_percent=60.0,
                    reset_at=1000,
                    window_minutes=60,
                ),
            },
            "secondary": {},
        },
    }

    result = build_additional_usage_summary(data)

    assert len(result) == 1
    summary = result[0]
    assert summary.primary_window is not None
    assert summary.primary_window.used_percent == pytest.approx(60.0)
    assert summary.secondary_window is None


def test_build_additional_usage_summary_multiple_limit_names():

    data: dict[str, dict[str, dict[str, AdditionalUsageHistory]]] = {
        "codex_other": {
            "primary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="codex_other",
                    used_percent=30.0,
                    reset_at=1000,
                    window_minutes=60,
                ),
            },
            "secondary": {},
        },
        "image_gen": {
            "primary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="image_gen",
                    metered_feature="image_generation",
                    used_percent=80.0,
                    reset_at=2000,
                    window_minutes=120,
                ),
            },
            "secondary": {},
        },
    }

    result = build_additional_usage_summary(data)

    assert len(result) == 2
    by_name = {s.limit_name: s for s in result}
    assert "codex_other" in by_name
    assert "image_gen" in by_name
    assert by_name["codex_other"].primary_window is not None
    assert by_name["codex_other"].primary_window.used_percent == pytest.approx(30.0)
    assert by_name["image_gen"].primary_window is not None
    assert by_name["image_gen"].primary_window.used_percent == pytest.approx(80.0)
    assert by_name["image_gen"].metered_feature == "image_generation"


def test_build_additional_usage_summary_no_secondary():

    data: dict[str, dict[str, dict[str, AdditionalUsageHistory]]] = {
        "codex_other": {
            "primary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="codex_other",
                    used_percent=50.0,
                    reset_at=1000,
                    window_minutes=60,
                ),
            },
            "secondary": {},
        },
    }

    result = build_additional_usage_summary(data)

    assert len(result) == 1
    assert result[0].secondary_window is None


def test_build_additional_usage_summary_empty_input():

    result = build_additional_usage_summary({})
    assert result == []


def test_build_additional_usage_summary_max_reset_at_and_window_minutes():

    data: dict[str, dict[str, dict[str, AdditionalUsageHistory]]] = {
        "codex_other": {
            "primary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="codex_other",
                    used_percent=10.0,
                    reset_at=500,
                    window_minutes=60,
                ),
                "acc2": _make_entry(
                    account_id="acc2",
                    limit_name="codex_other",
                    used_percent=20.0,
                    reset_at=900,
                    window_minutes=60,
                ),
                "acc3": _make_entry(
                    account_id="acc3",
                    limit_name="codex_other",
                    used_percent=30.0,
                    reset_at=100,
                    window_minutes=120,
                ),
            },
            "secondary": {},
        },
    }

    result = build_additional_usage_summary(data)

    assert len(result) == 1
    pw = result[0].primary_window
    assert pw is not None
    assert pw.used_percent == pytest.approx(20.0)
    assert pw.reset_at == 100
    assert pw.window_minutes == 120


def test_build_additional_usage_summary_none_reset_at():

    data: dict[str, dict[str, dict[str, AdditionalUsageHistory]]] = {
        "codex_other": {
            "primary": {
                "acc1": _make_entry(
                    account_id="acc1",
                    limit_name="codex_other",
                    used_percent=40.0,
                    reset_at=None,
                    window_minutes=None,
                ),
                "acc2": _make_entry(
                    account_id="acc2",
                    limit_name="codex_other",
                    used_percent=60.0,
                    reset_at=1000,
                    window_minutes=60,
                ),
            },
            "secondary": {},
        },
    }

    result = build_additional_usage_summary(data)

    assert len(result) == 1
    pw = result[0].primary_window
    assert pw is not None
    assert pw.used_percent == pytest.approx(50.0)
    assert pw.reset_at == 1000
    assert pw.window_minutes == 60


def test_additional_quota_window_readmits_exhausted_none_reset_row():

    from app.modules.accounts.service import _additional_quota_window

    entry = _make_entry(
        account_id="acc",
        limit_name="claude_top_thinking",
        used_percent=100.0,
        reset_at=None,
        window_minutes=300,
    )

    window = _additional_quota_window(entry, now_epoch=1_700_000_000)

    assert window is not None
    assert window.used_percent == 0.0
    assert window.reset_at is None


def test_additional_quota_window_preserves_bounded_future_exhaustion():
    from app.modules.accounts.service import _additional_quota_window

    now_epoch = 1_700_000_000
    entry = _make_entry(
        account_id="acc",
        limit_name="claude_top_thinking",
        used_percent=100.0,
        reset_at=now_epoch + 3600,
        window_minutes=300,
    )

    window = _additional_quota_window(entry, now_epoch=now_epoch)

    assert window is not None
    assert window.used_percent == 100.0
    assert window.reset_at == now_epoch + 3600
