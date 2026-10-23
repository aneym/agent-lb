from __future__ import annotations

from app.core.auth import DEFAULT_EMAIL, clean_account_identity_part, extract_id_token_claims, resolve_seat_identity
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.clients.oauth import OAuthTokens
from app.core.providers import OPENAI_PROVIDER_NAME
from app.db.models import Account
from app.modules.accounts.identity_repository import AccountIdentityConflictError
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth.callbacks import ReauthSeatMismatchError
from app.modules.oauth.token_account import account_from_tokens
from app.modules.proxy.account_cache import (
    clear_account_routing_unavailable,
    get_account_selection_cache,
    propagate_account_routing_change,
)


class OAuthAccountPersistence:
    async def _persist_tokens(
        self, tokens: OAuthTokens, *, provider_name: str = OPENAI_PROVIDER_NAME, intended_account_id: str | None = None
    ) -> None:
        account = account_from_tokens(tokens, provider_name, self._encryptor)
        if intended_account_id is not None:
            existing = await self._accounts_repo.get_by_id(intended_account_id)
            if existing is None or existing.provider != account.provider:
                raise AccountIdentityConflictError(account.email)
            account.id = intended_account_id
        if self._repo_factory:
            async with self._repo_factory() as repo:
                saved = await self._save_oauth_account(repo, account, intended_account_id=intended_account_id)
                saved_id = saved.id
        else:
            saved = await self._save_oauth_account(
                self._accounts_repo, account, intended_account_id=intended_account_id
            )
            saved_id = saved.id
        clear_account_routing_unavailable(saved_id)
        await self._invalidate_account_routing_caches()

    async def _save_oauth_account(
        self, repo: AccountsRepository, account: Account, *, intended_account_id: str | None
    ) -> Account:
        if intended_account_id is None:
            return await repo.upsert_account_slot(
                account, preserve_unknown_workspace_duplicates=False, preserve_identity_slots=True
            )
        intended = await repo.get_by_id(intended_account_id)
        if intended is None or intended.provider != account.provider:
            raise ReauthSeatMismatchError(None, account.email)
        if account.provider != OPENAI_PROVIDER_NAME:
            identity_matches = (
                intended.chatgpt_account_id == account.chatgpt_account_id
                if intended.chatgpt_account_id is not None
                else intended.email == account.email and account.email != DEFAULT_EMAIL
            )
            if not identity_matches:
                raise ReauthSeatMismatchError(intended.email, account.email)
            saved = await repo.replace_reauthorized(intended_account_id, account)
            if saved is None:
                raise ReauthSeatMismatchError(intended.email, account.email)
            return saved
        intended_user_id = intended.chatgpt_user_id
        intended_claims = None
        if intended_user_id is None:
            try:
                intended_token = self._encryptor.decrypt(intended.id_token_encrypted)
                intended_claims = extract_id_token_claims(intended_token)
                intended_user_id = resolve_seat_identity(intended_claims, intended_claims.auth)
            except Exception:
                intended_user_id = None
        if intended_claims is None:
            try:
                intended_claims = extract_id_token_claims(self._encryptor.decrypt(intended.id_token_encrypted))
            except Exception:
                intended_claims = None
        intended_workspace = clean_account_identity_part(intended.workspace_id or intended.workspace_label)
        callback_workspace = clean_account_identity_part(account.workspace_id or account.workspace_label)
        callback_claims = None
        try:
            callback_claims = extract_id_token_claims(self._encryptor.decrypt(account.id_token_encrypted))
        except Exception:
            callback_claims = None
        intended_seat_ids = {
            clean_account_identity_part(value)
            for value in (intended_user_id, intended_claims.sub if intended_claims else None)
            if clean_account_identity_part(value)
        }
        callback_seat_ids = {
            clean_account_identity_part(value)
            for value in (account.chatgpt_user_id, callback_claims.sub if callback_claims else None)
            if clean_account_identity_part(value)
        }
        workspace_matches = (
            intended.chatgpt_account_id is None or intended.chatgpt_account_id == account.chatgpt_account_id
        )
        if workspace_matches and intended.chatgpt_account_id is None and (intended_workspace is not None):
            workspace_matches = bool(callback_workspace is not None and callback_workspace == intended_workspace)
        seat_matches = bool(intended_seat_ids & callback_seat_ids)
        if not workspace_matches or not seat_matches:
            raise ReauthSeatMismatchError(intended.email, account.email)
        saved = await repo.replace_reauthorized(intended_account_id, account)
        if saved is None:
            raise ReauthSeatMismatchError(intended.email, account.email)
        return saved

    async def _invalidate_account_routing_caches(self) -> None:
        get_account_selection_cache().invalidate()
        get_api_key_cache().clear()
        await propagate_account_routing_change()
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)
