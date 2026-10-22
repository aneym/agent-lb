from __future__ import annotations

import time
from typing import cast

from app.core import usage as usage_core
from app.core.balancer import (
    PERMANENT_FAILURE_CODES,
    QUOTA_EXCEEDED_COOLDOWN_SECONDS,
    account_status_for_permanent_failure,
)
from app.core.clients.usage import UsageFetchError
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.usage.models import UsagePayload, UsageWindow
from app.core.utils.request_id import get_request_id
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.auth_contracts import AccountsRepositoryPort
from app.modules.accounts.auth_manager import AuthManager
from app.modules.usage.refresh_ports import (
    AccountsRepositoryWithStatusComparePort,
    AdditionalUsageRepositoryPort,
    UsageRepositoryPort,
)
from app.modules.usage.refresh_rules import (
    clean_optional,
    copy_refresh_sensitive_account_state,
    epoch_now,
    logger,
    reset_time,
    window_has_available_quota,
    window_is_exhausted,
)
from app.modules.usage.repository import AdditionalUsageRepository


class UsageAccountSync:
    def __init__(
        self,
        usage_repo: UsageRepositoryPort,
        accounts_repo: AccountsRepositoryPort | None = None,
        additional_usage_repo: AdditionalUsageRepositoryPort | AdditionalUsageRepository | None = None,
    ) -> None:
        self._usage_repo = usage_repo
        self._accounts_repo = accounts_repo
        self._additional_usage_repo = additional_usage_repo
        self._accounts_repo = accounts_repo
        self._encryptor = TokenEncryptor()
        self._auth_manager = AuthManager(accounts_repo) if accounts_repo else None

    async def _freshness_usage_entry(self, account: Account, latest: UsageHistory | None) -> UsageHistory | None:
        if latest is not None:
            return latest
        if usage_core.capacity_for_plan(account.plan_type, "monthly") is None:
            return None
        return await self._usage_repo.latest_entry_for_account(account.id, window="monthly")

    async def _deactivate_for_client_error(self, account: Account, exc: UsageFetchError) -> None:
        if not self._auth_manager:
            return
        reason = f"Usage API error: HTTP {exc.status_code} - {exc.message}"
        status = (
            account_status_for_permanent_failure(exc.code)
            if exc.code in PERMANENT_FAILURE_CODES
            else AccountStatus.DEACTIVATED
        )
        logger.warning(
            "Marking account unavailable due to client error account_id=%s account_status=%s status=%s "
            "message=%s request_id=%s",
            account.id,
            status.value,
            exc.status_code,
            exc.message,
            get_request_id(),
        )
        repo = cast(AccountsRepositoryWithStatusComparePort, self._auth_manager._repo)
        updated = await repo.update_status_if_current(
            account.id,
            status,
            reason,
            expected_status=account.status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not updated:
            await self._sync_account_from_repo(account)
            return
        account.status = status
        account.deactivation_reason = reason

    async def _sync_identity_metadata(self, account: Account, payload: UsagePayload) -> bool:
        expected_refresh_token_encrypted = account.refresh_token_encrypted
        next_plan_type = coerce_account_plan_type(payload.plan_type, account.plan_type or "free")
        payload_workspace_id = clean_optional(payload.workspace_id)
        next_workspace_id = payload_workspace_id or account.workspace_id
        next_workspace_label = clean_optional(payload.workspace_label) or account.workspace_label
        next_seat_type = clean_optional(payload.seat_type) or account.seat_type
        if self._auth_manager and payload_workspace_id and not account.workspace_id:
            slot_taken = await self._auth_manager._repo.workspace_slot_taken(
                account_id=account.id,
                email=account.email,
                chatgpt_account_id=account.chatgpt_account_id,
                workspace_id=payload_workspace_id,
            )
            if slot_taken:
                logger.warning(
                    "Usage payload reported workspace_id=%s for legacy account_id=%s, but that slot "
                    "is already owned by another account; skipping usage payload",
                    payload_workspace_id,
                    account.id,
                )
                return False
        if (
            next_plan_type == account.plan_type
            and next_workspace_id == account.workspace_id
            and next_workspace_label == account.workspace_label
            and next_seat_type == account.seat_type
        ):
            return True

        account.plan_type = next_plan_type
        account.workspace_id = next_workspace_id
        account.workspace_label = next_workspace_label
        account.seat_type = next_seat_type
        if not self._auth_manager:
            return True

        updated = await self._auth_manager._repo.update_tokens(
            account.id,
            access_token_encrypted=account.access_token_encrypted,
            refresh_token_encrypted=account.refresh_token_encrypted,
            id_token_encrypted=account.id_token_encrypted,
            last_refresh=account.last_refresh,
            plan_type=account.plan_type,
            email=account.email,
            chatgpt_account_id=account.chatgpt_account_id,
            workspace_id=account.workspace_id,
            workspace_label=account.workspace_label,
            seat_type=account.seat_type,
            expected_refresh_token_encrypted=expected_refresh_token_encrypted,
        )
        if not updated:
            stored = await self._auth_manager._repo.reload_by_id(account.id)
            if stored is not None:
                copy_refresh_sensitive_account_state(account, stored)
        return True

    async def _recover_quota_status_from_usage(
        self,
        account: Account,
        *,
        primary: UsageWindow | None,
        secondary: UsageWindow | None,
        monthly: UsageWindow | None = None,
    ) -> None:
        if not self._auth_manager:
            return
        if account.status == AccountStatus.RATE_LIMITED:
            long_window = monthly or secondary
            if primary is None and monthly is None:
                return
            if primary is not None and not window_has_available_quota(primary):
                return
            if primary is None and (long_window is None or not window_has_available_quota(long_window)):
                return
            if long_window is not None and not window_has_available_quota(long_window):
                return
            target_status = AccountStatus.ACTIVE
            target_reset_at = None
            expected_status = AccountStatus.RATE_LIMITED
        elif account.status == AccountStatus.QUOTA_EXCEEDED:
            if account.blocked_at is not None and time.time() < account.blocked_at + QUOTA_EXCEEDED_COOLDOWN_SECONDS:
                return
            long_window = monthly or secondary
            windows = [window for window in (primary, long_window) if window is not None]
            if long_window is None or not window_has_available_quota(long_window):
                return
            if primary is not None and window_is_exhausted(primary):
                target_status = AccountStatus.RATE_LIMITED
                target_reset_at = reset_time(primary.reset_at, primary.reset_after_seconds, epoch_now())
            else:
                if any(window_is_exhausted(window) for window in windows):
                    return
                target_status = AccountStatus.ACTIVE
                target_reset_at = None
            if not any(window_has_available_quota(window) for window in windows):
                return
            expected_status = AccountStatus.QUOTA_EXCEEDED
        else:
            return

        repo = cast(AccountsRepositoryWithStatusComparePort, self._auth_manager._repo)
        updated = await repo.update_status_if_current(
            account.id,
            target_status,
            None,
            target_reset_at,
            blocked_at=None,
            expected_status=expected_status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not updated:
            await self._sync_account_from_repo(account)
            return
        account.status = target_status
        account.deactivation_reason = None
        account.reset_at = target_reset_at
        account.blocked_at = None

    async def _sync_account_from_repo(self, account: Account) -> None:
        if not self._accounts_repo:
            return
        stored = await self._accounts_repo.get_by_id(account.id)
        if stored is None:
            return
        copy_refresh_sensitive_account_state(account, stored)
