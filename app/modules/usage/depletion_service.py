from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import blake2b
from typing import TypeAlias

from app.core.usage.depletion import (
    EWMAState,
    classify_risk,
    compute_burn_rate,
    compute_depletion_risk,
    compute_safe_usage_percent,
    ewma_update,
)
from app.core.utils.time import naive_utc_to_epoch, utcnow

# Per-account cache key: (account_id, limit_name, window).
_StateKey: TypeAlias = tuple[str, str, str]
_RowEdgeSignature = tuple[int | None, datetime, float, float | None, int | None]


@dataclass(frozen=True)
class _HistorySignature:
    row_count: int
    first: _RowEdgeSignature
    latest: _RowEdgeSignature
    content_digest: str | None


class _SignedHistory(list):
    def __init__(self, rows: Iterable, signature: _HistorySignature) -> None:
        super().__init__(rows)
        self.depletion_history_signature = signature


# In-memory EWMA state: keyed by (account_id, limit_name, window)
# Persists across requests; resets on process restart.
_ewma_states: dict[_StateKey, EWMAState] = {}
# Parallel signature map used to memoize EWMA rebuilds across dashboard polls
# (issue #537). When the in-window history is unchanged between requests we
# reuse the cached EWMAState instead of replaying the full history.
_history_signatures: dict[_StateKey, _HistorySignature] = {}


@dataclass
class DepletionMetrics:
    risk: float
    risk_level: str  # "safe" | "warning" | "danger" | "critical"
    rate_per_second: float
    burn_rate: float
    safe_usage_percent: float  # budget line position
    projected_exhaustion_at: datetime | None
    seconds_until_exhaustion: float | None
    account_id: str | None = None


@dataclass
class AggregateDepletionMetrics:
    risk: float
    risk_level: str
    burn_rate: float
    safe_usage_percent: float
    projected_exhaustion_at: datetime | None
    seconds_until_exhaustion: float | None
    # Attribution: the single account whose projected depletion is worst.
    # ``risk``/``risk_level`` above are pool-level (mean across accounts) —
    # one account burning fast must not label a mostly-idle pool critical.
    worst_account_id: str | None = None
    worst_risk: float | None = None
    worst_risk_level: str | None = None
    account_count: int = 0


def compute_depletion_for_account(
    account_id: str,
    limit_name: str,
    window: str,
    history: list,  # list of objects with: used_percent, recorded_at, reset_at, window_minutes
    now: datetime | None = None,
) -> DepletionMetrics | None:
    """
    Compute depletion metrics for a single account using EWMA.

    - history: list of usage entries ordered by recorded_at ASC
    - Returns None if insufficient data (<2 data points) or rate is unknown
    - Uses module-level _ewma_states for in-memory state
    """
    if not history:
        return None

    now = now or utcnow()
    key: _StateKey = (account_id, limit_name, window)

    if len(history) < 2:
        # Only one in-window sample — seed the EWMA but don't compute
        # depletion.  Reset any cached state so we never derive a rate
        # from an out-of-window sample plus this one.
        entry = history[0]
        _ewma_states[key] = ewma_update(
            None, entry.used_percent, naive_utc_to_epoch(entry.recorded_at), reset_at=entry.reset_at
        )
        _history_signatures[key] = _history_signature_from_edges(history)
        return None

    signature = _history_signature(history)
    cached_state = _ewma_states.get(key)
    cached_signature = _history_signatures.get(key)

    if cached_state is not None and cached_signature == signature:
        # Same in-window history as the last call — reuse the cached EWMA
        # state instead of replaying every row.  Time-dependent fields below
        # (risk, safe_usage_percent, projected_exhaustion_at) are still
        # recomputed from `now`, so dashboard polls remain live.
        state: EWMAState | None = cached_state
    else:
        state = _rebuild_ewma_state(history)
        if state is not None:
            _ewma_states[key] = state
            _history_signatures[key] = signature

    if state is None or state.rate is None:
        return None

    latest = history[-1]
    used_percent = latest.used_percent

    seconds_until_reset = 0.0
    if latest.reset_at is not None:
        seconds_until_reset = max(0.0, latest.reset_at - naive_utc_to_epoch(now))
        if seconds_until_reset == 0.0:
            # Window has already reset — the stale used_percent is
            # meaningless.  Clear EWMA state so next refresh starts fresh.
            _ewma_states.pop(key, None)
            return None
    elif latest.window_minutes is not None:
        # Without reset_at we cannot know when the window started.  Use
        # the full window duration as a conservative upper bound rather
        # than guessing from the first observed sample (which may appear
        # mid-window and dramatically underestimate remaining time).
        seconds_until_reset = float(latest.window_minutes * 60)

    total_window_seconds = (latest.window_minutes * 60) if latest.window_minutes else 0.0
    seconds_elapsed = max(0.0, total_window_seconds - seconds_until_reset)

    risk = compute_depletion_risk(used_percent, state.rate, seconds_until_reset)
    risk_level = classify_risk(risk)
    burn_rate = compute_burn_rate(state.rate, 100.0 - used_percent, seconds_until_reset)
    safe_pct = compute_safe_usage_percent(seconds_elapsed, total_window_seconds)

    projected_exhaustion_at = None
    seconds_until_exhaustion = None
    if state.rate > 0 and seconds_until_reset > 0:
        remaining = 100.0 - used_percent
        secs = remaining / state.rate
        if secs <= seconds_until_reset:
            seconds_until_exhaustion = secs
            projected_exhaustion_at = now + timedelta(seconds=secs)
        # else: exhaustion falls after the window resets — leave as None

    return DepletionMetrics(
        risk=risk,
        risk_level=risk_level,
        rate_per_second=state.rate,
        burn_rate=burn_rate,
        safe_usage_percent=safe_pct,
        projected_exhaustion_at=projected_exhaustion_at,
        seconds_until_exhaustion=seconds_until_exhaustion,
        account_id=account_id,
    )


def compute_aggregate_depletion(
    per_account_metrics: Sequence[DepletionMetrics | None],
) -> AggregateDepletionMetrics | None:
    """Aggregate depletion metrics across a pool of accounts.

    Pool risk is the mean of per-account risks: the balancer fails over, so a
    single account exhausting does not exhaust the pool. The worst account is
    still surfaced via the ``worst_*`` attribution fields so operators can see
    who drives an elevated pool risk. Exhaustion ETA fields carry the worst
    account's projection only while the pool itself is danger/critical, so a
    calm pool never renders an alarming exhaustion date.
    Returns None if no valid metrics.
    """
    valid = [m for m in per_account_metrics if m is not None]
    if not valid:
        return None

    worst = max(valid, key=lambda m: m.risk)
    pool_risk = sum(m.risk for m in valid) / len(valid)
    pool_level = classify_risk(pool_risk)
    pool_elevated = pool_level in ("danger", "critical")

    return AggregateDepletionMetrics(
        risk=pool_risk,
        risk_level=pool_level,
        burn_rate=sum(m.burn_rate for m in valid) / len(valid),
        safe_usage_percent=sum(m.safe_usage_percent for m in valid) / len(valid),
        projected_exhaustion_at=worst.projected_exhaustion_at if pool_elevated else None,
        seconds_until_exhaustion=worst.seconds_until_exhaustion if pool_elevated else None,
        worst_account_id=worst.account_id,
        worst_risk=worst.risk,
        worst_risk_level=worst.risk_level,
        account_count=len(valid),
    )


def reset_ewma_state() -> None:
    """Clear all in-memory EWMA state. Used for testing."""
    _ewma_states.clear()
    _history_signatures.clear()


def prune_depletion_cache(active_keys: Iterable[tuple[str, str, str]]) -> None:
    """Drop EWMA/signature cache entries outside the current account/window set."""
    keep = set(active_keys)
    for key in set(_ewma_states) | set(_history_signatures):
        if key not in keep:
            _ewma_states.pop(key, None)
            _history_signatures.pop(key, None)


def _rebuild_ewma_state(history: list) -> EWMAState | None:
    state: EWMAState | None = None
    for entry in history:
        ts = naive_utc_to_epoch(entry.recorded_at)
        state = ewma_update(state, entry.used_percent, ts, reset_at=entry.reset_at)
    return state


def attach_depletion_history_signature(history: Iterable) -> list:
    """Return a list carrying a compact content signature for cache checks.

    Dashboard code already iterates fetched rows while grouping/filtering them;
    attaching the digest there keeps cache-hit checks O(1) and avoids retaining
    a tuple per history row in the module-level cache.
    """
    rows = list(history)
    return _signed_history_from_rows(rows)


def filter_depletion_history_since(history: Iterable, cutoff: datetime) -> list:
    """Filter rows by cutoff and attach the cache signature in the same pass."""
    rows = []
    digest = blake2b(digest_size=16)
    for entry in history:
        if entry.recorded_at < cutoff:
            continue
        rows.append(entry)
        _update_history_digest(digest, entry)
    if not rows:
        return []
    return _SignedHistory(
        rows,
        _HistorySignature(
            row_count=len(rows),
            first=_row_edge_signature(rows[0]),
            latest=_row_edge_signature(rows[-1]),
            content_digest=digest.hexdigest(),
        ),
    )


def _history_signature(history: list) -> _HistorySignature:
    attached = getattr(history, "depletion_history_signature", None)
    if isinstance(attached, _HistorySignature):
        return attached
    return _history_signature_from_edges(history)


def _history_signature_from_rows(history: list) -> _HistorySignature:
    if not history:
        raise ValueError("history must not be empty")
    digest = blake2b(digest_size=16)
    for entry in history:
        _update_history_digest(digest, entry)
    return _HistorySignature(
        row_count=len(history),
        first=_row_edge_signature(history[0]),
        latest=_row_edge_signature(history[-1]),
        content_digest=digest.hexdigest(),
    )


def _signed_history_from_rows(history: list) -> list:
    return _SignedHistory(history, _history_signature_from_rows(history))


def _history_signature_from_edges(history: Sequence) -> _HistorySignature:
    if not history:
        raise ValueError("history must not be empty")
    return _HistorySignature(
        row_count=len(history),
        first=_row_edge_signature(history[0]),
        latest=_row_edge_signature(history[-1]),
        content_digest=None,
    )


def _row_edge_signature(entry) -> _RowEdgeSignature:
    return (
        getattr(entry, "id", None),
        entry.recorded_at,
        entry.used_percent,
        entry.reset_at,
        entry.window_minutes,
    )


def _update_history_digest(digest, entry) -> None:
    for value in _row_edge_signature(entry):
        digest.update(repr(value).encode("utf-8"))
        digest.update(b"\0")
    digest.update(b"\1")
