from __future__ import annotations

import hashlib
import socket
from datetime import datetime, timezone
from typing import Collection, Iterable

from app.core.balancer.failures import _format_retry_hint
from app.core.balancer.state import AccountState, ExcludedAccount, SelectionResult
from app.db.models import AccountStatus

PROBE_QUIET_SECONDS = 60.0
SECONDS_PER_DAY = 60 * 60 * 24
RATE_LIMIT_RESET_MAX_HORIZON_SECONDS = 366 * SECONDS_PER_DAY
RATE_LIMIT_RESET_ROUNDING_TOLERANCE_SECONDS = 1.0
ERROR_BACKOFF_THRESHOLD = 3
USAGE_LIMIT_REACHED = "usage_limit_reached"


def pool_usage_exhaustion(
    states: Iterable[AccountState],
    *,
    current: float,
    ignore_standard_quota: bool = False,
    ignore_standard_quota_account_ids: Collection[str] | None = None,
) -> SelectionResult | None:
    if ignore_standard_quota:
        return None
    ignored_account_ids = set(ignore_standard_quota_account_ids or ())

    def _primary_usage_evidence(state: AccountState) -> float | None:
        return state.priority_used_percent if state.priority_used_percent is not None else state.used_percent

    def _secondary_usage_evidence(state: AccountState) -> float | None:
        if state.limit_scoped_usage and state.priority_secondary_used_percent is None:
            return _primary_usage_evidence(state)
        return (
            state.priority_secondary_used_percent
            if state.priority_secondary_used_percent is not None
            else state.secondary_used_percent
        )

    def _usage_exhausted(state: AccountState) -> bool:
        if state.status not in (AccountStatus.QUOTA_EXCEEDED, AccountStatus.RATE_LIMITED):
            return False
        usage_values = (_primary_usage_evidence(state), _secondary_usage_evidence(state))
        return any((value is not None and float(value) >= 100.0 for value in usage_values))

    def _usage_exhausted_reset_at(state: AccountState) -> float | None:
        if state.status not in (AccountStatus.QUOTA_EXCEEDED, AccountStatus.RATE_LIMITED):
            return None
        candidates: list[float] = []
        primary_evidence = _primary_usage_evidence(state)
        secondary_evidence = _secondary_usage_evidence(state)
        if primary_evidence is not None and float(primary_evidence) >= 100.0 and (state.primary_reset_at is not None):
            candidates.append(float(state.primary_reset_at))
        if (
            secondary_evidence is not None
            and float(secondary_evidence) >= 100.0
            and (state.secondary_reset_at is not None)
        ):
            candidates.append(float(state.secondary_reset_at))
        if not candidates:
            return None
        return max(candidates)

    eligible = [
        state
        for state in states
        if not state.ignore_standard_quota
        and state.account_id not in ignored_account_ids
        and (state.status not in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED))
    ]
    if not eligible or any((not _usage_exhausted(state) for state in eligible)):
        return None
    reset_candidates = [reset_at for state in eligible if (reset_at := _usage_exhausted_reset_at(state)) is not None]
    resets_at = int(min(reset_candidates)) if reset_candidates else None
    message = "Usage limit reached"
    if resets_at is not None:
        message = _format_retry_hint(max(0.0, resets_at - current))
    return SelectionResult(account=None, error_message=message, error_code=USAGE_LIMIT_REACHED, resets_at=resets_at)


def _timestamp_as_utc(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _selection_failure(message: str, states: Iterable[AccountState], *, current: float) -> SelectionResult:
    excluded_accounts = [
        ExcludedAccount(
            account_id=state.account_id,
            status=state.status.value,
            reset_at=_timestamp_as_utc(state.reset_at if state.reset_at is not None else state.primary_reset_at),
            cooldown_until=_timestamp_as_utc(state.cooldown_until),
            deactivation_reason=state.deactivation_reason,
            plan_type=state.plan_type,
            transient_backoff_until=_timestamp_as_utc(
                state.last_error_at + min(300, 30 * 2 ** (state.error_count - 3))
                if state.error_count >= 3 and state.blocked_at is None and (state.last_error_at is not None)
                else None
            ),
        )
        for state in states
    ]
    future_recoveries = [
        recovery_at
        for excluded in excluded_accounts
        for recovery_at in (
            excluded.reset_at if excluded.status in ("rate_limited", "quota_exceeded") else None,
            excluded.cooldown_until,
            excluded.transient_backoff_until,
        )
        if recovery_at is not None and recovery_at.timestamp() > current
    ]
    return SelectionResult(
        account=None,
        error_message=message,
        excluded_accounts=excluded_accounts,
        retry_at=min(future_recoveries, default=None),
    )


def _oldest_due_probing_account(probing: Iterable[AccountState], *, current: float) -> AccountState | None:
    due = [
        state
        for state in probing
        if state.last_selected_at is None or current - state.last_selected_at >= PROBE_QUIET_SECONDS
    ]
    if not due:
        return None
    return min(
        due,
        key=lambda state: (
            state.last_selected_at if state.last_selected_at is not None else float("-inf"),
            state.account_id,
        ),
    )


_configured_replica_salt: str | None = None
_process_replica_salt: str | None = None


def configure_replica_salt(salt: str | None) -> None:
    global _configured_replica_salt
    normalized = salt.strip() if salt else ""
    _configured_replica_salt = normalized or None


def _default_replica_salt() -> str:
    global _process_replica_salt
    if _process_replica_salt is None:
        hostname = socket.gethostname().strip()
        _process_replica_salt = hostname or "codex-lb"
    return _process_replica_salt


def _effective_replica_salt(explicit: str | None) -> str:
    if explicit is not None and explicit.strip():
        return explicit.strip()
    if _configured_replica_salt is not None:
        return _configured_replica_salt
    return _default_replica_salt()


def _decorrelated_tie_breaker(account_id: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}\x00{account_id}".encode("utf-8")).hexdigest()


RATE_LIMITED_MIN_COOLDOWN_SECONDS = 30.0
