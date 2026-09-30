from datetime import UTC, datetime, timedelta

import pytest

from app.audit_tokens import quota_allocation


def receipt(stamp, **changes):
    return {
        "provider": "openai",
        "account_id": "account-a",
        "model": "unknown",
        "status": "success",
        "requested_at": stamp,
        "input_tokens": 50000,
        "cached_input_tokens": 10000,
        "output_tokens": 1000,
        "cost_usd": 0,
        **changes,
    }


def snapshot(stamp, used):
    return {
        "provider": "openai",
        "account_id": "account-a",
        "window": "primary",
        "window_minutes": 10080,
        "recorded_at": stamp,
        "used_percent": used,
        "reset_at": datetime(2026, 10, 1, tzinfo=UTC).timestamp(),
    }


@pytest.mark.parametrize("intermediate_snapshot", [False, True])
def test_openai_equal_token_mix_gets_equal_quota_regardless_of_price(intermediate_snapshot):
    start = datetime(2026, 9, 22, tzinfo=UTC)
    rows = [receipt(start + timedelta(minutes=10)), receipt(start + timedelta(minutes=40), cost_usd=1)]
    snapshots = [snapshot(start, 0)]
    if intermediate_snapshot:
        snapshots.append(snapshot(start + timedelta(minutes=20), 2))
    snapshots.append(snapshot(start + timedelta(minutes=50), 10))
    allocated, _ = quota_allocation(rows, snapshots, start, start + timedelta(hours=1))
    assert [item["weekly"] for item in allocated] == pytest.approx([5, 5])


def test_openai_snapshot_at_hour_boundary_closes_previous_utc_hour():
    start = datetime(2026, 9, 22, tzinfo=UTC)
    rows = [receipt(start + timedelta(minutes=10)), receipt(start + timedelta(hours=1, minutes=10))]
    snapshots = [snapshot(start, 0), snapshot(start + timedelta(hours=1), 10)]
    allocated, _ = quota_allocation(rows, snapshots, start, start + timedelta(hours=2))
    assert [item["weekly"] for item in allocated] == pytest.approx([10, 0])


def test_openai_empty_hour_and_failed_requests_are_unattributed():
    start = datetime(2026, 9, 22, tzinfo=UTC)
    rows = [receipt(start + timedelta(minutes=10)), receipt(start + timedelta(hours=1, minutes=10), status="error")]
    snapshots = [
        snapshot(start, 0),
        snapshot(start + timedelta(minutes=50), 3),
        snapshot(start + timedelta(hours=1, minutes=50), 8),
        snapshot(start + timedelta(hours=2, minutes=50), 15),
    ]
    allocated, quota = quota_allocation(rows, snapshots, start, start + timedelta(hours=3))
    assert [item["weekly"] for item in allocated] == pytest.approx([3, 0])
    assert sum(item["points"] for item in quota["unattributed"]) == pytest.approx(12)
    assert quota["providers"]["openai:weekly"]["quota_points"] == pytest.approx(15)


@pytest.mark.parametrize("odd_day_multiplier, expected_r2", [(1, 1), (2, -0.75)])
def test_binned_calibration_scores_odd_days_using_even_day_fit(odd_day_multiplier, expected_r2):
    start = datetime(2026, 9, 22, tzinfo=UTC)
    rows, snapshots = [], [snapshot(start - timedelta(minutes=1), 0)]
    used = 0
    for day in range(2):
        for hour, points in ((0, 1), (3, 2), (6, 3)):
            stamp = start + timedelta(days=day, hours=hour, minutes=10)
            rows.append(receipt(stamp, input_tokens=points, cached_input_tokens=0, output_tokens=0))
            used += points * (odd_day_multiplier if day else 1)
            snapshots.append(snapshot(stamp + timedelta(minutes=10), used))
    _, quota = quota_allocation(rows, snapshots, start, start + timedelta(days=2))
    fits = [fit for fit in quota["calibration"] if fit.get("bin_hours") in (1, 3)]
    assert {fit["bin_hours"] for fit in fits} == {1, 3}
    assert all(fit["holdout_r2"] == pytest.approx(expected_r2) for fit in fits)
    if odd_day_multiplier == 1:
        assert any("bin_hours" not in fit and fit["r_squared"] == pytest.approx(1) for fit in quota["calibration"])
