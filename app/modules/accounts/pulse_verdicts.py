from __future__ import annotations

import logging
from collections.abc import Callable, MutableMapping
from datetime import datetime

from app.core.audit.service import AuditService
from app.core.utils.time import to_utc_naive
from app.db.models import Account, AccountStatus
from app.modules.accounts import probes
from app.modules.accounts.pulse_backend import _AccountsRepositoryLike
from app.modules.accounts.subscription_status import (
    ACTIVE_SUBSCRIPTION_STATUS,
    CANCELED_SUBSCRIPTION_STATUS,
    normalize_subscription_status,
)
from app.modules.proxy.account_cache import get_account_selection_cache

logger = logging.getLogger("app.modules.accounts.pulse")


async def apply_verdict[Failure](
    repo: _AccountsRepositoryLike,
    account: Account,
    verdict: probes.ProbeVerdict,
    status: int,
    message: str | None,
    *,
    now: Callable[[], datetime],
    failures: MutableMapping[str, Failure],
    record_failure: Callable[[str], None],
) -> None:
    checked_at = to_utc_naive(now())
    date_label = checked_at.date().isoformat()
    subscription_canceled = normalize_subscription_status(account.subscription_status) == CANCELED_SUBSCRIPTION_STATUS
    if verdict is probes.ProbeVerdict.HEALTHY:
        failures.pop(account.id, None)
        if subscription_canceled:
            await repo.update_subscription_ledger(
                account.id,
                status=ACTIVE_SUBSCRIPTION_STATUS,
                next_charge_at=account.subscription_next_charge_at,
                current_period_end_at=account.subscription_current_period_end_at,
                amount=account.subscription_amount,
                currency=account.subscription_currency,
                last_verified_at=checked_at,
                notes=f"Account pulse verified the subscription is usable on {date_label}.",
            )
            get_account_selection_cache().invalidate()
            AuditService.log_async(
                "account_pulse_subscription_restored",
                details={"account_id": account.id, "probe_status_code": status},
            )
            logger.info("Account pulse restored subscription account_id=%s", account.id)
        if account.status in (AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED):
            updated = await repo.update_status_if_current(
                account.id,
                AccountStatus.ACTIVE,
                None,
                expected_status=account.status,
                expected_deactivation_reason=account.deactivation_reason,
                expected_reset_at=account.reset_at,
                expected_blocked_at=account.blocked_at,
            )
            if not updated:
                return
            get_account_selection_cache().invalidate()
            AuditService.log_async(
                "account_pulse_reactivated",
                details={
                    "account_id": account.id,
                    "previous_status": account.status.value,
                    "probe_status_code": status,
                },
            )
            logger.info("Account pulse reactivated account_id=%s", account.id)
        elif account.status == AccountStatus.ACTIVE and account.deactivation_reason is not None:
            updated = await repo.update_status_if_current(
                account.id,
                AccountStatus.ACTIVE,
                None,
                expected_status=account.status,
                expected_deactivation_reason=account.deactivation_reason,
                expected_reset_at=account.reset_at,
                expected_blocked_at=account.blocked_at,
            )
            if not updated:
                return
            get_account_selection_cache().invalidate()
            AuditService.log_async(
                "account_pulse_cleared_stale_deactivation_reason",
                details={
                    "account_id": account.id,
                    "stale_reason": account.deactivation_reason,
                    "probe_status_code": status,
                },
            )
            logger.info(
                "Account pulse cleared stale deactivation reason account_id=%s",
                account.id,
            )
        return
    if verdict is probes.ProbeVerdict.UNSUBSCRIBED:
        failures.pop(account.id, None)
        if account.status in (AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED):
            updated = await repo.update_status_if_current(
                account.id,
                AccountStatus.ACTIVE,
                None,
                expected_status=account.status,
                expected_deactivation_reason=account.deactivation_reason,
                expected_reset_at=account.reset_at,
                expected_blocked_at=account.blocked_at,
            )
            if not updated:
                return
            get_account_selection_cache().invalidate()
            AuditService.log_async(
                "account_pulse_reactivated",
                details={
                    "account_id": account.id,
                    "previous_status": account.status.value,
                    "probe_status_code": status,
                },
            )
            logger.info(
                "Account pulse cleared stale auth-failure status for unsubscribed account_id=%s",
                account.id,
            )
        if not subscription_canceled:
            await repo.update_subscription_ledger(
                account.id,
                status=CANCELED_SUBSCRIPTION_STATUS,
                next_charge_at=account.subscription_next_charge_at,
                current_period_end_at=account.subscription_current_period_end_at,
                amount=account.subscription_amount,
                currency=account.subscription_currency,
                last_verified_at=checked_at,
                notes=(
                    f"Account pulse detected a subscription refusal (HTTP {status}) on {date_label}: "
                    f"{message or 'no upstream message'}"
                ),
            )
            get_account_selection_cache().invalidate()
            AuditService.log_async(
                "account_pulse_subscription_canceled",
                details={
                    "account_id": account.id,
                    "probe_status_code": status,
                    "message": (message or "")[:200],
                },
            )
            logger.warning(
                "Account pulse marked subscription canceled account_id=%s status=%s",
                account.id,
                status,
            )
        return
    if verdict is probes.ProbeVerdict.DISCONNECTED:
        failures.pop(account.id, None)
        if account.status == AccountStatus.ACTIVE:
            await mark_disconnected(
                repo,
                account,
                reason=f"Account pulse: authentication rejected (HTTP {status})",
            )
        return
    record_failure(account.id)
    logger.info(
        "Account pulse inconclusive account_id=%s status=%s",
        account.id,
        status,
    )


async def mark_disconnected(
    repo: _AccountsRepositoryLike,
    account: Account,
    *,
    reason: str,
) -> None:
    updated = await repo.update_status_if_current(
        account.id,
        AccountStatus.REAUTH_REQUIRED,
        reason,
        expected_status=account.status,
        expected_deactivation_reason=account.deactivation_reason,
        expected_reset_at=account.reset_at,
        expected_blocked_at=account.blocked_at,
    )
    if not updated:
        return
    get_account_selection_cache().invalidate()
    AuditService.log_async(
        "account_pulse_reauth_required",
        details={"account_id": account.id, "reason": reason},
    )
    logger.warning("Account pulse marked reauth required account_id=%s reason=%s", account.id, reason)
