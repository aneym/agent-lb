from __future__ import annotations

from app.core.balancer import AccountState
from app.db.models import Account, AccountStatus


def has_selection_state_changes(
    account_map: dict[str, Account],
    states: list[AccountState],
    *,
    skip_account_ids: frozenset[str] = frozenset(),
) -> bool:
    for state in states:
        if state.ignore_standard_quota or state.account_id in skip_account_ids:
            continue
        account = account_map.get(state.account_id)
        if account is None:
            continue
        reset_at_int = int(state.reset_at) if state.reset_at else None
        blocked_at_int = int(state.blocked_at) if state.blocked_at else None
        persisted_status = (
            AccountStatus.EXCHANGE_UNCERTAIN if account.status == AccountStatus.EXCHANGE_UNCERTAIN else state.status
        )
        persisted_reason = (
            account.deactivation_reason
            if persisted_status == AccountStatus.EXCHANGE_UNCERTAIN
            else state.deactivation_reason
        )
        if (
            account.status != persisted_status
            or account.deactivation_reason != persisted_reason
            or account.reset_at != reset_at_int
            or account.blocked_at != blocked_at_int
        ):
            return True
    return False
