from __future__ import annotations

import pytest

from app.modules.proxy._service.compaction_breaker import (
    CompactionRetryBreaker,
    compaction_fingerprint,
)

pytestmark = pytest.mark.unit


def _clock(value: list[float]):
    return lambda: value[0]


def test_admits_up_to_threshold_then_rejects() -> None:
    now = [0.0]
    breaker = CompactionRetryBreaker(threshold=5, window_seconds=600.0, clock=_clock(now))
    fingerprint = "fp-identical"

    for expected_count in range(1, 6):
        admission = breaker.admit(fingerprint)
        assert admission.admitted is True
        assert admission.count == expected_count

    rejected = breaker.admit(fingerprint)
    assert rejected.admitted is False
    assert rejected.count == 5


def test_distinct_fingerprints_are_independent() -> None:
    now = [0.0]
    breaker = CompactionRetryBreaker(threshold=5, window_seconds=600.0, clock=_clock(now))

    for i in range(10):
        admission = breaker.admit(f"fp-{i}")
        assert admission.admitted is True


def test_window_expiry_readmits_after_rejection() -> None:
    now = [0.0]
    breaker = CompactionRetryBreaker(threshold=5, window_seconds=600.0, clock=_clock(now))
    fingerprint = "fp-window"

    for _ in range(5):
        assert breaker.admit(fingerprint).admitted is True
    assert breaker.admit(fingerprint).admitted is False

    now[0] += 600.1
    reopened = breaker.admit(fingerprint)
    assert reopened.admitted is True
    assert reopened.count == 1


def test_audit_flag_fires_once_per_window() -> None:
    now = [0.0]
    breaker = CompactionRetryBreaker(threshold=5, window_seconds=600.0, clock=_clock(now))
    fingerprint = "fp-audit"

    for _ in range(5):
        admission = breaker.admit(fingerprint)
        assert admission.should_audit is False

    first_rejection = breaker.admit(fingerprint)
    assert first_rejection.admitted is False
    assert first_rejection.should_audit is True

    second_rejection = breaker.admit(fingerprint)
    assert second_rejection.admitted is False
    assert second_rejection.should_audit is False

    now[0] += 600.1
    reopened = breaker.admit(fingerprint)
    assert reopened.admitted is True
    assert reopened.should_audit is False


def test_bounded_tracking_evicts_oldest_touched_fingerprint() -> None:
    now = [0.0]
    breaker = CompactionRetryBreaker(threshold=5, window_seconds=600.0, max_tracked=3, clock=_clock(now))

    breaker.admit("fp-a")
    breaker.admit("fp-b")
    breaker.admit("fp-c")
    assert len(breaker._windows) == 3

    breaker.admit("fp-d")
    assert len(breaker._windows) == 3
    assert "fp-a" not in breaker._windows
    assert "fp-d" in breaker._windows


def test_compaction_fingerprint_is_canonical_and_order_independent() -> None:
    fingerprint_a = compaction_fingerprint("gpt-5.5", [{"type": "compaction_trigger", "id": "x"}])
    fingerprint_b = compaction_fingerprint("gpt-5.5", [{"id": "x", "type": "compaction_trigger"}])
    assert fingerprint_a == fingerprint_b


def test_compaction_fingerprint_differs_for_distinct_input() -> None:
    fingerprint_a = compaction_fingerprint("gpt-5.5", [{"type": "compaction_trigger"}])
    fingerprint_b = compaction_fingerprint("gpt-5.5", [{"type": "compaction_trigger", "nonce": 1}])
    assert fingerprint_a != fingerprint_b
