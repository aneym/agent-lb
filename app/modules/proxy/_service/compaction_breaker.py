from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from app.core.types import JsonValue

# A healthy client never resends the identical compaction turn many times,
# because a successful compact changes the conversation input. More than
# this many admissions of the same fingerprint within the window means the
# client is stuck retrying a turn that never lands.
COMPACTION_RETRY_THRESHOLD = 5
COMPACTION_RETRY_WINDOW_SECONDS = 600.0
# Bounds the tracker's memory footprint; oldest-touched fingerprints are
# evicted first, well above realistic concurrent-compaction-loop counts.
COMPACTION_RETRY_MAX_TRACKED_FINGERPRINTS = 512


def compaction_fingerprint(model: JsonValue, input_items: JsonValue) -> str:
    """Fingerprint a compaction turn from its model and request input.

    Canonical JSON (sorted keys, no incidental whitespace) so semantically
    identical payloads hash identically regardless of key order.
    """
    canonical = json.dumps(
        {"model": model, "input": input_items},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _FingerprintWindow:
    window_start: float
    count: int = 0
    audited: bool = False


@dataclass(frozen=True, slots=True)
class CompactionAdmission:
    admitted: bool
    # True at most once per window: the first request that trips the
    # breaker for a given fingerprint, so callers record one audit event
    # per open rather than one per rejected request.
    should_audit: bool
    count: int


class CompactionRetryBreaker:
    """Bounded sliding-window tracker for repeated identical compaction turns.

    Not thread-safe by design — the app runs a single asyncio event loop, so
    a plain dict is sufficient (matches the convention in
    ``app.modules.proxy.account_cache``/``rate_limit_cache``).
    """

    def __init__(
        self,
        *,
        threshold: int = COMPACTION_RETRY_THRESHOLD,
        window_seconds: float = COMPACTION_RETRY_WINDOW_SECONDS,
        max_tracked: int = COMPACTION_RETRY_MAX_TRACKED_FINGERPRINTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._window_seconds = window_seconds
        self._max_tracked = max_tracked
        self._clock = clock
        self._windows: OrderedDict[str, _FingerprintWindow] = OrderedDict()

    def admit(self, fingerprint: str) -> CompactionAdmission:
        """Record an admission attempt and decide whether to admit it.

        Starts (or restarts, once the window has expired) a fresh window on
        the first sight of a fingerprint. Within an open window, the first
        ``threshold`` requests are admitted; any further request for the
        same fingerprint is rejected until the window expires.
        """
        now = self._clock()
        window = self._windows.get(fingerprint)
        if window is None or now - window.window_start >= self._window_seconds:
            window = _FingerprintWindow(window_start=now)
            self._windows[fingerprint] = window
        self._windows.move_to_end(fingerprint)

        if window.count >= self._threshold:
            should_audit = not window.audited
            window.audited = True
            self._evict_oldest_if_over_capacity()
            return CompactionAdmission(admitted=False, should_audit=should_audit, count=window.count)

        window.count += 1
        self._evict_oldest_if_over_capacity()
        return CompactionAdmission(admitted=True, should_audit=False, count=window.count)

    def _evict_oldest_if_over_capacity(self) -> None:
        while len(self._windows) > self._max_tracked:
            self._windows.popitem(last=False)

    def reset(self) -> None:
        """Clear all tracked fingerprint windows. Test-only reset hook for
        the process-wide singleton, mirroring ``AccountSelectionCache.invalidate``."""
        self._windows.clear()


_compaction_retry_breaker = CompactionRetryBreaker()


def get_compaction_retry_breaker() -> CompactionRetryBreaker:
    return _compaction_retry_breaker
