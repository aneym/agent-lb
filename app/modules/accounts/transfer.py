from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    claims_from_auth,
    generate_unique_account_id,
    parse_auth_json,
    token_expiry_epoch_ms,
)
from app.core.plan_types import coerce_account_plan_type
from app.core.providers import OPENAI_PROVIDER_NAME, get_anthropic_compat_profile, get_provider, normalize_provider_name
from app.core.providers.openrouter import OPENROUTER_PROVIDER_NAME
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.auth_manager import expiry_datetime
from app.modules.accounts.credits import (
    CREDITS_USAGE_WINDOW,
    credits_exhausted,
    fetch_openrouter_credits,
    window_from_parts,
)
from app.modules.accounts.errors import InvalidAuthJsonError
from app.modules.accounts.queries import AccountsQueries
from app.modules.accounts.schemas import (
    AccountApiKeyImportRequest,
    AccountAuthExportResponse,
    AccountAuthExportTokens,
    AccountExportResponse,
    AccountImportResponse,
    AccountOpenCodeAuthExportAccount,
    AccountOpenCodeAuthExportResponse,
    CodexAuthJson,
    CodexAuthTokens,
    OpenCodeAuthJson,
    OpenCodeOAuthAuth,
)
from app.modules.proxy.account_cache import (
    clear_account_routing_unavailable,
    get_account_selection_cache,
    mark_account_routing_unavailable,
)

logger = logging.getLogger(__name__)


class AccountsTransfer(AccountsQueries):
    async def export_opencode_auth(self, account_id: str) -> AccountOpenCodeAuthExportResponse | None:
        account = await self._get_visible_account(account_id)
        if account is None:
            return None
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        expires = token_expiry_epoch_ms(access_token) or 0
        return AccountOpenCodeAuthExportResponse(
            filename=_opencode_auth_export_filename(account),
            account=AccountOpenCodeAuthExportAccount(
                account_id=account.id, chatgpt_account_id=account.chatgpt_account_id, email=account.email
            ),
            auth_json=OpenCodeAuthJson(
                openai=OpenCodeOAuthAuth(
                    refresh=refresh_token, access=access_token, expires=expires, account_id=account.chatgpt_account_id
                )
            ),
        )

    async def export_auth(self, account_id: str) -> AccountAuthExportResponse | None:
        account = await self._get_visible_account(account_id)
        if account is None:
            return None
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        id_token = self._encryptor.decrypt(account.id_token_encrypted) if account.id_token_encrypted else ""
        expires = token_expiry_epoch_ms(access_token) or 0
        tokens = AccountAuthExportTokens(
            id_token=id_token, access_token=access_token, refresh_token=refresh_token, expires_at_ms=expires
        )
        codex_auth_json = CodexAuthJson(
            auth_mode="chatgpt",
            openai_api_key=None,
            tokens=CodexAuthTokens(
                id_token=id_token,
                access_token=access_token,
                refresh_token=refresh_token,
                account_id=account.chatgpt_account_id,
            ),
            last_refresh=account.last_refresh.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        )
        opencode_auth_json = OpenCodeAuthJson(
            openai=OpenCodeOAuthAuth(
                refresh=refresh_token, access=access_token, expires=expires, account_id=account.chatgpt_account_id
            )
        )
        return AccountAuthExportResponse(
            filename=_opencode_auth_export_filename(account),
            account=AccountOpenCodeAuthExportAccount(
                account_id=account.id, chatgpt_account_id=account.chatgpt_account_id, email=account.email
            ),
            tokens=tokens,
            codex_auth_json=codex_auth_json,
            opencode_auth_json=opencode_auth_json,
        )

    async def import_account(self, raw: bytes) -> AccountImportResponse:
        try:
            auth = parse_auth_json(raw)
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError, TypeError) as exc:
            raise InvalidAuthJsonError("Invalid auth.json payload") from exc
        provider = get_provider(OPENAI_PROVIDER_NAME)
        metadata = provider.account_metadata_from_id_token(auth.tokens.id_token)
        claims = claims_from_auth(auth)
        email = claims.email or metadata.email or DEFAULT_EMAIL
        raw_account_id = claims.account_id or metadata.account_id
        account_id = generate_unique_account_id(raw_account_id, email, claims.workspace_id, claims.workspace_label)
        plan_type = coerce_account_plan_type(claims.plan_type or metadata.plan_type, DEFAULT_PLAN)
        last_refresh = to_utc_naive(auth.last_refresh_at) if auth.last_refresh_at else utcnow()
        account = Account(
            id=account_id,
            provider=provider.name,
            chatgpt_account_id=raw_account_id,
            email=email,
            workspace_id=claims.workspace_id,
            workspace_label=claims.workspace_label,
            seat_type=claims.seat_type,
            chatgpt_user_id=claims.chatgpt_user_id,
            plan_type=plan_type,
            access_token_encrypted=self._encryptor.encrypt(auth.tokens.access_token),
            refresh_token_encrypted=self._encryptor.encrypt(auth.tokens.refresh_token),
            id_token_encrypted=self._encryptor.encrypt(auth.tokens.id_token),
            last_refresh=last_refresh,
            access_expires_at=expiry_datetime(auth.tokens.access_token),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )
        saved = await self._repo.upsert_account_slot(account)
        import_usage_refresh_allowed = await self._import_usage_refresh_allowed(saved)
        if not import_usage_refresh_allowed:
            await self._repo.update_status(
                saved.id, AccountStatus.PAUSED, "upstream_proxy_required_on_import", None, blocked_at=None
            )
            mark_account_routing_unavailable(saved.id)
            saved = await self._repo.get_by_id(saved.id) or saved
        if import_usage_refresh_allowed and self._usage_repo and self._usage_updater:
            latest_usage = await self._usage_repo.latest_by_account(window="primary")
            await self._usage_updater.refresh_accounts([saved], latest_usage)
        if saved.status == AccountStatus.ACTIVE:
            clear_account_routing_unavailable(saved.id)
        get_account_selection_cache().invalidate()
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,
            workspace_id=saved.workspace_id,
            workspace_label=saved.workspace_label,
            seat_type=saved.seat_type,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def import_api_key_account(self, payload: AccountApiKeyImportRequest) -> AccountImportResponse:
        provider_name = normalize_provider_name(payload.provider)
        try:
            profile = get_anthropic_compat_profile(provider_name)
        except ValueError as exc:
            raise ValueError(f"Provider {provider_name} does not support API-key account import") from exc
        defaults = profile.import_defaults
        if defaults is None:
            raise ValueError(f"Provider {provider_name} does not support API-key account import")
        provider = get_provider(provider_name)
        api_key = payload.api_key.get_secret_value().strip()
        if not api_key:
            raise ValueError("apiKey is required")
        if payload.refresh_token is None:
            refresh_material = api_key
        else:
            if not profile.supports_oauth_bundle_import:
                raise ValueError(f"Provider {provider_name} does not accept refreshToken on API-key import")
            refresh_material = payload.refresh_token.get_secret_value().strip()
            if not refresh_material:
                raise ValueError("refreshToken must not be blank")
        email = (payload.email or defaults.email).strip().lower()
        raw_account_id = (payload.account_id or defaults.account_id_seed).strip()
        account_id = generate_unique_account_id(raw_account_id, email)
        plan_type = coerce_account_plan_type(payload.plan_type, defaults.plan_type)
        credits_window = None
        if provider_name == OPENROUTER_PROVIDER_NAME:
            credits_window = window_from_parts(
                balance=payload.credits_balance, cap=payload.credits_cap, spent=payload.credits_spent
            )
            if credits_window is None:
                credits_window = await fetch_openrouter_credits(api_key)
        status = AccountStatus.ACTIVE
        if credits_window is not None and credits_exhausted(credits_window):
            status = AccountStatus.QUOTA_EXCEEDED
        account = Account(
            id=account_id,
            provider=provider.name,
            chatgpt_account_id=raw_account_id,
            email=email,
            workspace_id=None,
            workspace_label=None,
            seat_type=None,
            plan_type=plan_type,
            access_token_encrypted=self._encryptor.encrypt(api_key),
            refresh_token_encrypted=self._encryptor.encrypt(refresh_material),
            id_token_encrypted=None,
            last_refresh=utcnow(),
            status=status,
            deactivation_reason=None,
        )
        saved = await self._repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)
        alias_source = payload.alias if "alias" in payload.model_fields_set else defaults.alias
        if alias_source is not None:
            alias = alias_source.strip() or None
            await self._repo.update_alias(saved.id, alias)
            saved.alias = alias
        if credits_window is not None and self._usage_repo is not None:
            await self._usage_repo.add_entry(
                saved.id,
                0.0,
                provider=provider.name,
                window=CREDITS_USAGE_WINDOW,
                credits_has=True,
                credits_unlimited=False,
                credits_balance=credits_window.balance,
                credits_cap=credits_window.cap,
                credits_spent=credits_window.spent,
            )
        logger.info(
            "api_key_account_imported provider=%s account_id=%s exhausted=%s",
            provider.name,
            saved.id,
            credits_window is not None and credits_exhausted(credits_window),
        )
        get_account_selection_cache().invalidate()
        return AccountImportResponse(
            account_id=saved.id,
            email=saved.email,
            workspace_id=saved.workspace_id,
            workspace_label=saved.workspace_label,
            seat_type=saved.seat_type,
            plan_type=saved.plan_type,
            status=saved.status,
        )

    async def export_account(self, account_id: str) -> AccountExportResponse | None:
        account = await self._get_visible_account(account_id)
        if not account:
            return None
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        id_token = self._encryptor.decrypt(account.id_token_encrypted) if account.id_token_encrypted else ""
        auth_json = {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": id_token,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "account_id": account.chatgpt_account_id,
            },
            "last_refresh": account.last_refresh.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        }
        return AccountExportResponse(
            account_id=account.id,
            email=account.email,
            workspace_id=account.workspace_id,
            workspace_label=account.workspace_label,
            seat_type=account.seat_type,
            plan_type=account.plan_type,
            status=account.status.value,
            auth_json=json.dumps(auth_json, indent=2),
        )


def _opencode_auth_export_filename(account: Account) -> str:
    source = account.email or account.id
    safe = "".join((char if char.isalnum() or char in "._-" else "-" for char in source)).strip("-._")
    return f"opencode-auth-{safe or account.id}.json"
