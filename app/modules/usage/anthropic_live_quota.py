from __future__ import annotations

import logging
from collections.abc import Mapping

from app.core.anthropic.quota_headers import account_quota_windows
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.usage.repository import UsageRepository

logger = logging.getLogger(__name__)


async def observe_anthropic_quota(account_id: str, headers: Mapping[str, str]) -> None:
    try:
        await _persist_observation(account_id, headers)
    except Exception:
        logger.warning("Failed to persist Anthropic live quota account_id=%s", account_id, exc_info=True)


async def _persist_observation(account_id: str, headers: Mapping[str, str]) -> None:
    windows = account_quota_windows(headers)
    if not windows:
        return
    observed_at = utcnow()
    async with get_background_session() as session:
        accounts = AccountsRepository(session)
        account = await accounts.get_by_id(account_id)
        if account is None or account.provider != "anthropic":
            return
        usage = UsageRepository(session)
        exhausted_resets = []
        for window, (percent, reset_at, minutes) in windows.items():
            previous = await usage.latest_entry_for_account(account_id, window=window)
            if previous is not None and previous.recorded_at > observed_at:
                continue
            if reset_at is None and previous is not None:
                reset_at = previous.reset_at
            await usage.add_entry(
                account_id,
                percent,
                provider="anthropic",
                window=window,
                reset_at=reset_at,
                window_minutes=minutes,
                recorded_at=observed_at,
                credits_has=previous.credits_has if previous else None,
                credits_unlimited=previous.credits_unlimited if previous else None,
                credits_balance=previous.credits_balance if previous else None,
                credits_cap=previous.credits_cap if previous else None,
                credits_spent=previous.credits_spent if previous else None,
            )
            if percent >= 100 and reset_at is not None and reset_at > naive_utc_to_epoch(observed_at):
                exhausted_resets.append(reset_at)
        if exhausted_resets and account.status in {
            AccountStatus.ACTIVE,
            AccountStatus.RATE_LIMITED,
            AccountStatus.QUOTA_EXCEEDED,
        }:
            await accounts.update_status_if_current(
                account_id,
                AccountStatus.RATE_LIMITED,
                account.deactivation_reason,
                max(exhausted_resets),
                expected_status=account.status,
                expected_deactivation_reason=account.deactivation_reason,
                expected_reset_at=account.reset_at,
                expected_blocked_at=account.blocked_at,
            )
    get_account_selection_cache().invalidate()
