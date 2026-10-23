from __future__ import annotations

import asyncio
import time

from app.core.auth.refresh import RefreshError
from app.core.providers import OPENAI_PROVIDER_NAME, get_provider
from app.db.models import Account
from app.modules.accounts.auth_support import RefreshAdmissionLeasePort as RefreshAdmissionLeasePort
from app.modules.accounts.auth_support import _snapshot as _snapshot
from app.modules.accounts.auth_support import logger as logger


class AccountRefreshPreflight:
    async def _acquire_refresh_admission_bounded(
        self, account: Account, deadline: float | None
    ) -> RefreshAdmissionLeasePort | None:
        if self._acquire_refresh_admission is None:
            return None
        if deadline is None:
            return await self._acquire_refresh_admission()
        admission_wait = deadline - time.monotonic()
        if admission_wait <= 0:
            raise RefreshError(
                "refresh_claim_timeout",
                f"Token refresh for account {account.id} exhausted its refresh budget before token-refresh admission could be acquired",
                False,
                transport_error=True,
            )
        try:
            return await asyncio.wait_for(self._acquire_refresh_admission(), timeout=admission_wait)
        except asyncio.TimeoutError as exc:
            raise RefreshError(
                "refresh_claim_timeout",
                f"Token refresh for account {account.id} exhausted its refresh budget waiting for token-refresh admission on a saturated concurrency gate",
                False,
                transport_error=True,
            ) from exc

    async def _ensure_chatgpt_account_id(self, account: Account) -> Account:
        if account.chatgpt_account_id:
            return account
        if account.provider != OPENAI_PROVIDER_NAME or account.id_token_encrypted is None:
            return account
        try:
            id_token = self._encryptor.decrypt(account.id_token_encrypted)
        except Exception:
            return account
        raw_account_id = get_provider(account.provider).account_metadata_from_id_token(id_token).account_id
        if not raw_account_id:
            return account
        expected_refresh_token_encrypted = account.refresh_token_encrypted
        account.chatgpt_account_id = raw_account_id
        try:
            updated = await self._repo.update_tokens(
                account.id,
                access_token_encrypted=account.access_token_encrypted,
                refresh_token_encrypted=account.refresh_token_encrypted,
                id_token_encrypted=account.id_token_encrypted,
                last_refresh=account.last_refresh,
                plan_type=account.plan_type,
                email=account.email,
                chatgpt_account_id=raw_account_id,
                workspace_id=account.workspace_id,
                workspace_label=account.workspace_label,
                seat_type=account.seat_type,
                expected_refresh_token_encrypted=expected_refresh_token_encrypted,
            )
            if not updated:
                latest = await self._repo.reload_by_id(account.id)
                if latest is not None:
                    return _snapshot(self._repo, latest)
        except Exception:
            logger.warning(
                "Failed to persist chatgpt_account_id account_id=%s",
                self._diagnostic_value(account.id),
                exc_info=None if self._redact_sensitive_details else True,
            )
        return account
