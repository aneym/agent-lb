from __future__ import annotations

from app.core.config.settings import get_settings
from app.core.providers import (
    OPENAI_PROVIDER_NAME,
    get_provider,
)
from app.db import session as db_session
from app.db.models import Account, AccountStatus
from app.modules.accounts.auth_contracts import AccountNotOwnedError, is_locally_owned
from app.modules.accounts.auth_exchange import AuthManager as AuthExchange
from app.modules.accounts.auth_singleflight import _REFRESH_SINGLEFLIGHT, _refresh_singleflight_key, logger
from app.modules.accounts.auth_tokens import (
    _account_needs_refresh,
    _exchange_timeout_seconds,
    _refresh_token_material_fingerprint,
    _snapshot,
    access_token_hard_expired,
)
from app.modules.accounts.repository import EXCHANGE_RESET_REASONS


class AuthManager(AuthExchange):
    async def ensure_fresh(self, account: Account, *, force: bool = False, recover_uncertain: bool = False) -> Account:
        settings = get_settings()

        latest = await self._repo.reload_by_id(account.id)
        if latest is not None:
            account = latest

        account = _snapshot(self._repo, account)
        if not is_locally_owned(account, settings):
            if force or access_token_hard_expired(self._encryptor, account):
                raise AccountNotOwnedError(account.id, account.owner_instance, settings.local_instance_id)
            return await self._ensure_chatgpt_account_id(account)
        recover_exchange = False
        if account.status == AccountStatus.EXCHANGE_UNCERTAIN:
            if recover_uncertain and isinstance(getattr(self._repo, "session", None), db_session.AsyncSession):
                intent = await self._repo.exchange_intent_window(account.id, _exchange_timeout_seconds())
                recover_exchange = (
                    intent is not None
                    and intent.token_hash
                    == _refresh_token_material_fingerprint(self._encryptor, account.refresh_token_encrypted)
                    and intent.ready
                    and (
                        intent.reason in EXCHANGE_RESET_REASONS
                        or (not intent.replay and intent.reason not in {"unsaved", "auto_exhausted"})
                    )
                )
            if not force and not recover_exchange and not access_token_hard_expired(self._encryptor, account):
                return await self._ensure_chatgpt_account_id(account)
        if force or recover_exchange or _account_needs_refresh(self._encryptor, account):
            account = await _REFRESH_SINGLEFLIGHT.run(
                _refresh_singleflight_key(self._encryptor, account),
                lambda: self._run_refresh(account),
            )
        return await self._ensure_chatgpt_account_id(account)

    async def _run_refresh(self, account: Account) -> Account:

        if self._refresh_repo_factory is None:
            return await self.refresh_account(account)
        async with self._refresh_repo_factory() as repo:
            owned = AuthManager(repo, acquire_refresh_admission=self._acquire_refresh_admission)
            return await owned.refresh_account(account)

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
            logger.warning("Failed to persist chatgpt_account_id account_id=%s", account.id, exc_info=True)
        return account
