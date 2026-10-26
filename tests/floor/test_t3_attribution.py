from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.t3_threads.attribution import apportion, hour_index, token_equivalents, weekly_rises

BASE = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
RESET = int(BASE.timestamp()) + 3 * 86400
H = hour_index(BASE)


def at(hours: int, minutes: int = 10) -> datetime:
    return BASE + timedelta(hours=hours, minutes=minutes)


@pytest.mark.parametrize(
    ("samples", "factor", "expected"),
    [
        ([(at(0), 10, RESET), (at(1), 12, RESET)], 1.0, {H + 1: 2.0}),
        ([(at(0), 10, RESET), (at(0, 20), 11, RESET), (at(1), 15, RESET)], 2.0, {H: 2.0, H + 1: 8.0}),
        ([(at(0), 90, RESET), (at(1), 3, RESET + 7 * 86400)], 1.0, {}),
        ([(at(0), 30, RESET), (at(1), 20, RESET), (at(2), 25, RESET)], 1.0, {}),
        ([(at(0), 30, RESET), (at(1), 29, RESET), (at(2), 30, RESET), (at(3), 31, RESET)], 1.0, {H + 3: 1.0}),
        (
            [
                (at(0), 90, RESET),
                (at(1), 3, RESET + 7 * 86400),
                (at(2), 2, RESET + 7 * 86400),
                (at(3), 4, RESET + 7 * 86400),
            ],
            1.0,
            {H + 3: 1.0},
        ),
        ([(at(1), 12, RESET), (at(0), 10, RESET)], 1.0, {H + 1: 2.0}),
        ([(at(0), 5, RESET)], 1.0, {}),
    ],
    ids=[
        "one rise",
        "two rises scaled by plan factor",
        "reset is not a rise",
        "drop then partial recovery is no rise",
        "flicker counts only the new high",
        "high-water restarts after reset",
        "unsorted",
        "single",
    ],
)
def test_weekly_rises_count_only_new_highs_inside_one_reset(samples, factor, expected):
    assert weekly_rises(samples, factor) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("rises", "loads", "shares", "unexplained"),
    [
        ({("a", 5): 3.0}, {("a", 5): {"x": 2.0, "y": 1.0}}, {("a", 5): {"x": 2.0, "y": 1.0}}, 0.0),
        ({("a", 5): 4.0}, {("a", 4): {"x": 1.0}}, {("a", 5): {"x": 4.0}}, 0.0),
        ({("a", 5): 4.0}, {("a", 3): {"x": 1.0}}, {}, 4.0),
        ({("a", 5): 4.0}, {("a", 5): {"x": 0.0}, ("a", 4): {"y": 2.0}}, {("a", 5): {"y": 4.0}}, 0.0),
        ({("a", 5): 4.0}, {("b", 5): {"x": 1.0}}, {}, 4.0),
        (
            {("a", 5): 1.0, ("b", 5): 2.0},
            {("a", 5): {"x": 1.0}, ("b", 5): {"x": 1.0, "y": 1.0}},
            {("a", 5): {"x": 1.0}, ("b", 5): {"x": 1.0, "y": 1.0}},
            0.0,
        ),
    ],
    ids=[
        "split by weight",
        "previous hour takes it",
        "two hours back is unexplained",
        "zero weight falls back",
        "other account's load ignored",
        "accounts apportioned separately",
    ],
)
def test_apportion_splits_each_account_hour_by_weight(rises, loads, shares, unexplained):
    got_shares, got_unexplained = apportion(rises, loads)
    assert got_shares == shares
    assert got_unexplained == pytest.approx(unexplained)


def test_token_equivalents_weight_output_and_cache():
    assert token_equivalents(100, 10, 8, 1000) == pytest.approx(100 + 50 + 10 + 100)
