from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.core.auth.refresh import RefreshError
from app.core.balancer import PERMANENT_FAILURE_CODES, account_status_for_permanent_failure
from app.db.models import Account, AccountStatus
from app.modules.accounts.auth_support import (
    _FINAL_PERSIST_MAX_ATTEMPTS,
    _FINAL_PERSIST_RETRY_BASE_SECONDS,
    _TOKEN_CAS_MAX_ATTEMPTS,
    _adopt_account_row,
    _decrypt_refresh_token_plaintext,
    _is_genuine_peer_rotation,
    _refresh_token_material_fingerprint,
    logger,
)
from app.modules.proxy.account_cache import get_account_selection_cache, mark_account_routing_unavailable


class AccountRefreshPersistence:
    async def _persist_refreshed_tokens(
        self,
        account: Account,
        *,
        write: Callable[[bytes], Awaitable[bool]],
        expected_refresh_token_encrypted: bytes,
        deadline: float | None = None,
    ) -> Account | None:
        consumed_plaintext = _decrypt_refresh_token_plaintext(self._encryptor, expected_refresh_token_encrypted)
        expected = expected_refresh_token_encrypted
        deadline_elapsed = False
        for _attempt in range(_TOKEN_CAS_MAX_ATTEMPTS):
            if await write(expected):
                return None
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                return None
            latest_plaintext = _decrypt_refresh_token_plaintext(self._encryptor, latest.refresh_token_encrypted)
            if consumed_plaintext is None or latest_plaintext is None:
                logger.warning(
                    "Token-refresh compare-and-set for account_id=%s missed and the stored refresh-token plaintext could not be decrypted for comparison; surfacing a transient error rather than risking a clobber",
                    self._diagnostic_value(account.id),
                )
                raise RefreshError(
                    "token_persist_conflict",
                    f"Token-refresh compare-and-set for account_id={account.id} could not persist rotated tokens (stored refresh-token plaintext was undecryptable)",
                    False,
                    transport_error=True,
                )
            if latest_plaintext != consumed_plaintext:
                return _adopt_account_row(account, latest)
            expected = latest.refresh_token_encrypted
            if deadline is not None and time.monotonic() >= deadline:
                deadline_elapsed = True
                break
        for final_attempt in range(_FINAL_PERSIST_MAX_ATTEMPTS):
            if await write(expected):
                return None
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                return None
            latest_plaintext = _decrypt_refresh_token_plaintext(self._encryptor, latest.refresh_token_encrypted)
            if _is_genuine_peer_rotation(consumed_plaintext, latest_plaintext):
                return _adopt_account_row(account, latest)
            if consumed_plaintext is None or latest_plaintext is None:
                break
            expected = latest.refresh_token_encrypted
            if final_attempt < _FINAL_PERSIST_MAX_ATTEMPTS - 1:
                await asyncio.sleep(_FINAL_PERSIST_RETRY_BASE_SECONDS * 2**final_attempt)
        return await self._flag_persist_conflict_reauth(
            account,
            expected_refresh_token_encrypted=expected,
            consumed_plaintext=consumed_plaintext,
            deadline_elapsed=deadline_elapsed,
        )

    async def _flag_persist_conflict_reauth(
        self,
        account: Account,
        *,
        expected_refresh_token_encrypted: bytes,
        consumed_plaintext: str | None,
        deadline_elapsed: bool,
    ) -> Account | None:
        status = AccountStatus.REAUTH_REQUIRED
        reason = "Refresh token persistence conflict; stored token is stale - re-login required"
        expected = expected_refresh_token_encrypted
        for _attempt in range(_FINAL_PERSIST_MAX_ATTEMPTS):
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                return None
            latest_plaintext = _decrypt_refresh_token_plaintext(self._encryptor, latest.refresh_token_encrypted)
            if _is_genuine_peer_rotation(consumed_plaintext, latest_plaintext):
                return _adopt_account_row(account, latest)
            expected = latest.refresh_token_encrypted
            applied = await self._repo.update_status_if_current(
                account.id,
                status,
                reason,
                expected_status=latest.status,
                expected_deactivation_reason=latest.deactivation_reason,
                expected_reset_at=latest.reset_at,
                expected_refresh_token_encrypted=expected,
            )
            if applied:
                account.status = status
                account.deactivation_reason = reason
                mark_account_routing_unavailable(account.id)
                get_account_selection_cache().invalidate()
                logger.warning(
                    "Token-refresh compare-and-set for account_id=%s could not persist the freshly rotated token after the dedicated final-persist retries (%s); flagged the account REAUTH_REQUIRED via the guarded status path so it is explicitly repaired rather than left holding a consumed token that a blind retry would permanently knock out",
                    self._diagnostic_value(account.id),
                    "claim/caller deadline elapsed" if deadline_elapsed else "same-plaintext storm exhausted",
                )
                return account
        logger.warning(
            "Token-refresh compare-and-set for account_id=%s could not persist the freshly rotated token nor flag REAUTH_REQUIRED after the dedicated final-persist retries; surfacing a transient conflict so the caller retries the whole refresh rather than risking a clobber",
            self._diagnostic_value(account.id),
        )
        raise RefreshError(
            "token_persist_conflict",
            f"Token-refresh compare-and-set for account_id={account.id} could not persist rotated tokens nor flag REAUTH_REQUIRED after a final guarded attempt missed on unchanged material",
            False,
            transport_error=True,
        )

    async def _handle_permanent_refresh_failure(
        self, account: Account, exc: RefreshError, attempted_fingerprint: str, *, deadline: float | None = None
    ) -> Account | None:
        latest = await self._repo.get_by_id_fresh(account.id)
        if latest is None:
            return None
        if (
            _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
            != attempted_fingerprint
        ):
            return _adopt_account_row(account, latest)
        reason = PERMANENT_FAILURE_CODES.get(exc.code, exc.message)
        status = account_status_for_permanent_failure(exc.code)
        for attempt in range(_TOKEN_CAS_MAX_ATTEMPTS):
            if attempt > 0 and deadline is not None and (time.monotonic() >= deadline):
                logger.warning(
                    "Permanent refresh-failure status CAS for account_id=%s code=%s exceeded the claim/caller deadline after %d attempt(s); surfacing a transient conflict and releasing the claim rather than looping past the budget",
                    self._diagnostic_value(account.id),
                    exc.code,
                    attempt,
                )
                raise RefreshError(
                    "status_downgrade_conflict",
                    f"Permanent refresh-failure status downgrade for account_id={account.id} (code={exc.code}) exceeded the refresh claim/caller deadline before it could persist REAUTH_REQUIRED",
                    False,
                    transport_error=True,
                ) from exc
            applied = await self._repo.update_status_if_current(
                account.id,
                status,
                reason,
                expected_status=latest.status,
                expected_deactivation_reason=latest.deactivation_reason,
                expected_reset_at=latest.reset_at,
                expected_refresh_token_encrypted=latest.refresh_token_encrypted,
            )
            if applied:
                account.status = status
                account.deactivation_reason = reason
                mark_account_routing_unavailable(account.id)
                get_account_selection_cache().invalidate()
                return None
            latest = await self._repo.get_by_id_fresh(account.id)
            if latest is None:
                return None
            if (
                _refresh_token_material_fingerprint(self._encryptor, latest.refresh_token_encrypted)
                != attempted_fingerprint
            ):
                return _adopt_account_row(account, latest)
        logger.warning(
            "Permanent refresh-failure status CAS for account_id=%s code=%s kept missing on unchanged token material after %d attempts; surfacing a transient error so the caller retries rather than running the unguarded permanent-failure mark that could clobber a concurrent peer rotation",
            self._diagnostic_value(account.id),
            exc.code,
            _TOKEN_CAS_MAX_ATTEMPTS,
        )
        raise RefreshError(
            "status_downgrade_conflict",
            f"Permanent refresh-failure status downgrade for account_id={account.id} (code={exc.code}) could not persist REAUTH_REQUIRED after {_TOKEN_CAS_MAX_ATTEMPTS} attempts due to concurrent writes",
            False,
            transport_error=True,
        ) from exc
