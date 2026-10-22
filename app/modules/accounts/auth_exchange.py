from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext

from app.core.auth import DEFAULT_PLAN
from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import (
    RefreshError,
    TokenRefreshResult,
    classify_refresh_error,
    refresh_access_token,
)
from app.core.balancer import account_status_for_permanent_failure, permanent_failure_reason
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    KIMI_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    Provider,
    ProviderLookupError,
    get_provider,
)
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import utcnow
from app.db import session as db_session
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts import refresh_lock
from app.modules.accounts.auth_contracts import (
    AccountNotOwnedError,
    AccountsRepositoryPort,
    RefreshAdmissionLeasePort,
    is_locally_owned,
)
from app.modules.accounts.auth_singleflight import logger
from app.modules.accounts.auth_tokens import (
    call_with_supported_optional_kwargs,
    clean_optional,
    exchange_timeout_seconds,
    expiry_datetime,
    refresh_token_material_changed,
    refresh_token_material_fingerprint,
    snapshot,
)
from app.modules.accounts.exchange_persist import (
    dedicated_accounts_repo,
    mark_exchange_unsaved,
    persist_budget_seconds,
    persist_exchange_result,
)
from app.modules.accounts.repository import EXCHANGE_RESET_REASONS
from app.modules.proxy.account_cache import get_account_selection_cache


class AuthExchange:
    def __init__(
        self,
        repo: AccountsRepositoryPort,
        *,
        acquire_refresh_admission: Callable[[], Awaitable[RefreshAdmissionLeasePort]] | None = None,
        refresh_repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepositoryPort]] | None = None,
    ) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()
        self._acquire_refresh_admission = acquire_refresh_admission

        self._refresh_repo_factory = refresh_repo_factory

    async def refresh_account(self, account: Account) -> Account:
        try:
            provider = get_provider(account.provider)
        except ProviderLookupError as exc:
            raise RefreshError("unsupported_provider", str(exc), True) from exc
        async with refresh_lock._cross_process_refresh_lock(account.id):
            return await self._refresh_account_locked(account, provider)

    async def _refresh_account_locked(self, account: Account, provider: Provider) -> Account:
        expected_refresh_token_encrypted = account.refresh_token_encrypted
        latest = await self._repo.reload_by_id(account.id)
        if latest is not None and refresh_token_material_changed(
            self._encryptor, latest.refresh_token_encrypted, expected_refresh_token_encrypted
        ):
            if not is_locally_owned(latest, get_settings()):
                raise AccountNotOwnedError(latest.id, latest.owner_instance, get_settings().local_instance_id)

            return snapshot(self._repo, latest)
        if latest is None:
            if isinstance(getattr(self._repo, "session", None), db_session.AsyncSession):
                raise RefreshError("account_missing", "Account no longer exists", False)
            latest = account
        if not is_locally_owned(latest, get_settings()):
            raise AccountNotOwnedError(latest.id, latest.owner_instance, get_settings().local_instance_id)
        account = latest
        account_id = account.id
        expected_refresh_token_encrypted = latest.refresh_token_encrypted
        token_hash = refresh_token_material_fingerprint(self._encryptor, expected_refresh_token_encrypted)
        intent_supported = isinstance(getattr(self._repo, "session", None), db_session.AsyncSession)
        persist_repo = dedicated_accounts_repo if intent_supported else lambda: nullcontext(self._repo)
        replay = False
        if intent_supported:
            timeout = exchange_timeout_seconds()
            intent = await self._repo.exchange_intent_window(account.id, timeout)
            if intent is not None and intent.token_hash != token_hash:
                if account.status != AccountStatus.EXCHANGE_UNCERTAIN and await self._repo.drop_stale_exchange_intent(
                    account.id, intent.token_hash, expected_refresh_token_encrypted
                ):
                    logger.info("Dropped stale exchange intent account_id=%s", account.id)
                    intent = None
                else:
                    raise RefreshError("exchange_intent_conflict", "A previous exchange is unresolved", False)
            if intent is not None:
                if account.status in {AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED}:
                    raise RefreshError("exchange_uncertain", "Permanent auth failure requires operator recovery", False)
                if not intent.ready or (
                    intent.reason not in EXCHANGE_RESET_REASONS
                    and (intent.replay or intent.reason in {"unsaved", "auto_exhausted"})
                ):
                    if account.status != AccountStatus.EXCHANGE_UNCERTAIN:
                        await self._repo.mark_exchange_uncertain(
                            account.id, token_hash, expected_refresh_token_encrypted
                        )
                        get_account_selection_cache().invalidate()
                    raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False)
                replay = True
            elif account.status == AccountStatus.EXCHANGE_UNCERTAIN:
                raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False)
            if not await self._repo.begin_exchange(
                account_id,
                token_hash,
                expected_refresh_token_encrypted,
                replay=replay,
                timeout_seconds=timeout if replay else None,
            ):
                latest = await self._repo.reload_by_id(account_id)
                if latest is not None and not is_locally_owned(latest, get_settings()):
                    raise AccountNotOwnedError(latest.id, latest.owner_instance, get_settings().local_instance_id)
                if latest is not None and refresh_token_material_changed(
                    self._encryptor, latest.refresh_token_encrypted, expected_refresh_token_encrypted
                ):
                    return snapshot(self._repo, latest)
                if await self._repo.exchange_intent_hash(account_id) == token_hash:
                    await self._repo.mark_exchange_uncertain(account_id, token_hash, expected_refresh_token_encrypted)
                    get_account_selection_cache().invalidate()
                raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False)
        elif account.status == AccountStatus.EXCHANGE_UNCERTAIN:
            raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False)
        exchange_started = False

        def mark_exchange_started() -> None:
            nonlocal exchange_started
            exchange_started = True

        try:
            refresh_token = self._encryptor.decrypt(expected_refresh_token_encrypted)
            result = await self._refresh_tokens(
                refresh_token, account=account, provider=provider, on_exchange_start=mark_exchange_started
            )
        except BaseException as exc:
            if intent_supported:
                phase = exc.phase if isinstance(exc, RefreshError) else ExchangePhase.AMBIGUOUS
                if (
                    not exchange_started
                    or phase == ExchangePhase.PRE_SEND
                    or (
                        phase == ExchangePhase.ANSWERED
                        and isinstance(exc, RefreshError)
                        and not exc.is_permanent
                        and not (exc.status_code is not None and exc.status_code >= 500)
                    )
                ):
                    await self._repo.abort_exchange(account.id, token_hash)
                    if replay:
                        await self._repo.update_status(
                            account.id,
                            AccountStatus.ACTIVE,
                            expected_refresh_token_encrypted=expected_refresh_token_encrypted,
                        )
                    if isinstance(exc, RefreshError):
                        raise
                    raise
                if phase == ExchangePhase.ANSWERED and isinstance(exc, RefreshError) and exc.is_permanent:
                    await self._repo.abort_exchange(account.id, token_hash)
                    status = account_status_for_permanent_failure(exc.code)
                    await self._repo.update_status(
                        account.id,
                        status,
                        permanent_failure_reason(exc.code),
                        expected_refresh_token_encrypted=expected_refresh_token_encrypted,
                    )
                    logger.info("Refresh exchange resolved account_id=%s outcome=reauth_required", account.id)
                    raise
                if isinstance(exc, RefreshError) and exc.code == "invalid_response":
                    await mark_exchange_unsaved(
                        persist_repo, account.id, account.provider, token_hash, expected_refresh_token_encrypted
                    )
                else:
                    await self._repo.mark_exchange_uncertain(account.id, token_hash, expected_refresh_token_encrypted)
                get_account_selection_cache().invalidate()
                raise RefreshError("exchange_uncertain", "Refresh exchange outcome uncertain", False) from exc
            if not isinstance(exc, RefreshError):
                raise
            is_permanent = exc.is_permanent or classify_refresh_error(exc.code)
            if is_permanent:
                exc.is_permanent = True
                latest = await self._repo.reload_by_id(account.id)
                if latest is not None and refresh_token_material_changed(
                    self._encryptor,
                    latest.refresh_token_encrypted,
                    expected_refresh_token_encrypted,
                ):
                    return snapshot(self._repo, latest)
                reason = permanent_failure_reason(exc.code)
                status = account_status_for_permanent_failure(exc.code)
                status_updated = await self._repo.update_status(
                    account.id,
                    status,
                    reason,
                    expected_refresh_token_encrypted=(
                        latest.refresh_token_encrypted if latest is not None else expected_refresh_token_encrypted
                    ),
                )
                if not status_updated:
                    current = await self._repo.reload_by_id(account.id)
                    if current is not None and refresh_token_material_changed(
                        self._encryptor,
                        current.refresh_token_encrypted,
                        expected_refresh_token_encrypted,
                    ):
                        return snapshot(self._repo, current)
                    raise
                account.status = status
                account.deactivation_reason = reason
                logger.warning(
                    "OAuth refresh requires reauthentication account_id=%s provider=%s code=%s",
                    account.id,
                    account.provider,
                    exc.code,
                )
            raise

        if provider.requires_id_token and not result.id_token:
            if intent_supported:
                await mark_exchange_unsaved(
                    persist_repo, account.id, account.provider, token_hash, expected_refresh_token_encrypted
                )
            raise RefreshError("invalid_response", "Refresh response missing id token", False)

        new_access_token_encrypted = self._encryptor.encrypt(result.access_token)
        new_refresh_token_encrypted = self._encryptor.encrypt(result.refresh_token)
        account.access_token_encrypted = new_access_token_encrypted
        account.refresh_token_encrypted = new_refresh_token_encrypted
        if result.id_token:
            account.id_token_encrypted = self._encryptor.encrypt(result.id_token)
        else:
            account.id_token_encrypted = None
        account.last_refresh = utcnow()
        account.access_expires_at = expiry_datetime(result.access_token, result.expires_in, now=account.last_refresh)
        if result.account_id:
            account.chatgpt_account_id = result.account_id
        if result.plan_type is not None:
            account.plan_type = coerce_account_plan_type(
                result.plan_type,
                account.plan_type or DEFAULT_PLAN,
            )
        elif not account.plan_type:
            account.plan_type = DEFAULT_PLAN
        if result.email:
            account.email = result.email
        incoming_workspace_id = clean_optional(result.workspace_id)
        current_workspace_id = clean_optional(account.workspace_id)
        next_workspace_id = current_workspace_id
        if incoming_workspace_id and current_workspace_id and current_workspace_id != incoming_workspace_id:
            logger.warning(
                "Refresh payload reported workspace_id=%s for account_id=%s while existing "
                "workspace_id=%s is already set; keeping slot identity",
                incoming_workspace_id,
                account.id,
                current_workspace_id,
            )
            next_workspace_id = current_workspace_id
        elif not current_workspace_id and incoming_workspace_id:
            slot_taken = await self._repo.workspace_slot_taken(
                account_id=account.id,
                email=account.email,
                chatgpt_account_id=account.chatgpt_account_id,
                workspace_id=incoming_workspace_id,
            )
            if slot_taken:
                logger.warning(
                    "Refresh payload reported workspace_id=%s for legacy account_id=%s, but that slot "
                    "is already owned by another account; keeping unknown workspace",
                    incoming_workspace_id,
                    account.id,
                )
            else:
                next_workspace_id = incoming_workspace_id
                account.workspace_id = next_workspace_id
        workspace_matches_current_slot = incoming_workspace_id is None or incoming_workspace_id == next_workspace_id
        if workspace_matches_current_slot and result.workspace_label:
            account.workspace_label = result.workspace_label
        if workspace_matches_current_slot and result.seat_type:
            account.seat_type = result.seat_type

        if isinstance(getattr(self._repo, "session", None), db_session.AsyncSession):
            self._repo.session.expunge(account)

        try:
            tokens_updated = await persist_exchange_result(
                persist_repo,
                account.id,
                account.provider,
                {
                    "access_token_encrypted": account.access_token_encrypted,
                    "refresh_token_encrypted": account.refresh_token_encrypted,
                    "id_token_encrypted": account.id_token_encrypted,
                    "last_refresh": account.last_refresh,
                    "plan_type": account.plan_type,
                    "email": account.email,
                    "chatgpt_account_id": account.chatgpt_account_id,
                    "workspace_id": next_workspace_id,
                    "workspace_label": account.workspace_label,
                    "seat_type": account.seat_type,
                    "access_expires_at": account.access_expires_at,
                },
                expected_refresh_token_encrypted,
                token_hash if intent_supported else None,
                persist_budget_seconds(),
            )
        except Exception:
            tokens_updated = None
        if tokens_updated is False:
            latest = await self._repo.reload_by_id(account.id)
            if latest is not None and refresh_token_material_changed(
                self._encryptor, latest.refresh_token_encrypted, expected_refresh_token_encrypted
            ):
                return snapshot(self._repo, latest)
        if not tokens_updated:
            if intent_supported:
                await mark_exchange_unsaved(
                    persist_repo, account.id, account.provider, token_hash, expected_refresh_token_encrypted
                )
            raise RefreshError("refresh_unsaved", "Refreshed tokens could not be stored", False)
        if replay:
            logger.info("Refresh exchange resolved account_id=%s outcome=active", account.id)
        get_account_selection_cache().invalidate()
        if hasattr(self._repo, "session"):
            stored = await self._repo.reload_by_id(account.id)
            if stored is not None:
                account = stored
        return snapshot(self._repo, account)

    async def _refresh_tokens(
        self,
        refresh_token: str,
        *,
        account: Account,
        provider: Provider,
        on_exchange_start: Callable[[], None] | None = None,
    ) -> TokenRefreshResult:
        refresh_lease: RefreshAdmissionLeasePort | None = None
        if self._acquire_refresh_admission is not None:
            refresh_lease = await self._acquire_refresh_admission()
        try:
            if provider.name not in (OPENAI_PROVIDER_NAME, ANTHROPIC_PROVIDER_NAME, KIMI_PROVIDER_NAME):
                if on_exchange_start:
                    on_exchange_start()
                return await provider.refresh_access_token(refresh_token)
            if provider.name != OPENAI_PROVIDER_NAME:
                return await call_with_supported_optional_kwargs(
                    provider.refresh_access_token,
                    refresh_token,
                    optional_kwargs={"on_exchange_start": on_exchange_start},
                )
            async with get_background_session() as session:
                try:
                    route = await resolve_upstream_route(
                        session,
                        account_id=account.id,
                        operation="token_refresh",
                        scope="account",
                        encryptor=self._encryptor,
                    )
                except UpstreamProxyRouteError as exc:
                    raise RefreshError(
                        "upstream_proxy_unavailable",
                        f"Upstream proxy route unavailable: {exc.reason}",
                        False,
                        transport_error=True,
                        upstream_proxy_fail_closed_reason=exc.reason,
                    ) from exc
            return await call_with_supported_optional_kwargs(
                refresh_access_token,
                refresh_token,
                optional_kwargs={
                    "route": route,
                    "allow_direct_egress": route is None,
                    "on_exchange_start": on_exchange_start,
                },
            )
        finally:
            if refresh_lease is not None:
                refresh_lease.release()
