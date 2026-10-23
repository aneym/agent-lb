from __future__ import annotations

from uuid import uuid4

from app.core.auth.refresh import RefreshError
from app.core.clients.usage import (
    ConsumeRateLimitResetCreditResponse,
    UsageFetchError,
    consume_rate_limit_reset_credit,
    fetch_usage,
)
from app.core.upstream_proxy import ResolvedUpstreamRoute, resolve_upstream_route
from app.core.usage.models import UsagePayload
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.schemas import (
    AccountUsageResetConsumeResponse,
    AccountUsageResetCredits,
    AccountUsageResetCreditsResponse,
)
from app.modules.accounts.service_errors import (
    AccountUsageResetConsumeUnavailableError,
    AccountUsageResetCreditsUnavailableError,
)
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store


class AccountUsageResetService:
    async def get_usage_reset_credits(self, account_id: str) -> AccountUsageResetCreditsResponse | None:
        account = await self._get_visible_account(account_id)
        if account is None:
            return None
        if account.status in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            raise AccountUsageResetCreditsUnavailableError(
                f"Account is {account.status.value} and cannot fetch usage reset credits"
            )
        if self._auth_manager is not None:
            try:
                account = await self._auth_manager.ensure_fresh(account)
            except RefreshError as exc:
                raise AccountUsageResetCreditsUnavailableError(
                    f"Account credentials could not be refreshed: {exc.message}"
                ) from exc
        if not account.chatgpt_account_id:
            raise AccountUsageResetCreditsUnavailableError("Account is missing ChatGPT account identity")
        payload = await self._fetch_usage_payload_for_reset_credits(account)
        reset_credits = payload.rate_limit_reset_credits
        available_count = reset_credits.available_count if reset_credits is not None else None
        return AccountUsageResetCreditsResponse(
            account_id=account.id,
            rate_limit_reset_credits=AccountUsageResetCredits(available_count=max(0, int(available_count or 0))),
        )

    async def _fetch_usage_payload_for_reset_credits(self, account: Account) -> UsagePayload:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        route = await self._resolve_usage_reset_credit_route(account, operation="usage_reset_credits_read")
        try:
            return await fetch_usage(
                access_token=access_token,
                account_id=account.chatgpt_account_id,
                route=route,
                allow_direct_egress=route is None,
            )
        except UsageFetchError as exc:
            if exc.status_code != 401 or self._auth_manager is None:
                raise
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError as refresh_exc:
                raise AccountUsageResetCreditsUnavailableError(
                    f"Account credentials could not be refreshed: {refresh_exc.message}"
                ) from refresh_exc
            if not account.chatgpt_account_id:
                raise AccountUsageResetCreditsUnavailableError("Account is missing ChatGPT account identity") from exc
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            retry_route = await self._resolve_usage_reset_credit_route(account, operation="usage_reset_credits_read")
            return await fetch_usage(
                access_token=access_token,
                account_id=account.chatgpt_account_id,
                route=retry_route,
                allow_direct_egress=retry_route is None,
            )

    async def consume_usage_reset_credit(
        self, account_id: str, *, redeem_request_id: str | None = None
    ) -> AccountUsageResetConsumeResponse | None:
        account = await self._get_visible_account(account_id)
        if account is None:
            return None
        if account.status in (AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            raise AccountUsageResetConsumeUnavailableError(
                f"Account is {account.status.value} and cannot consume usage reset credits"
            )
        if self._auth_manager is not None:
            try:
                account = await self._auth_manager.ensure_fresh(account)
            except RefreshError as exc:
                raise AccountUsageResetConsumeUnavailableError(
                    f"Account credentials could not be refreshed: {exc.message}"
                ) from exc
        if not account.chatgpt_account_id:
            raise AccountUsageResetConsumeUnavailableError("Account is missing ChatGPT account identity")
        primary_before, secondary_before = await self._latest_usage_percents(account_id)
        status_before = account.status.value
        upstream_response, account = await self._consume_usage_reset_credit(
            account, redeem_request_id=redeem_request_id
        )
        if upstream_response.code in ("reset", "already_redeemed", "no_credit", "nothing_to_reset"):
            await get_rate_limit_reset_credits_store().invalidate(account_id)
        usage_written = False
        if upstream_response.code in ("reset", "already_redeemed") and self._usage_repo and self._usage_updater:
            usage_written = await self._usage_updater.force_refresh(account, ignore_refresh_disabled=True)
            get_account_selection_cache().invalidate()
        refreshed = await self._repo.get_by_id(account_id) or account
        primary_after, secondary_after = await self._latest_usage_percents(account_id)
        return AccountUsageResetConsumeResponse(
            status="reset",
            account_id=account_id,
            code=upstream_response.code,
            windows_reset=upstream_response.windows_reset,
            usage_written=usage_written,
            primary_used_percent_before=primary_before,
            primary_used_percent_after=primary_after,
            secondary_used_percent_before=secondary_before,
            secondary_used_percent_after=secondary_after,
            account_status_before=status_before,
            account_status_after=refreshed.status.value,
        )

    async def _consume_usage_reset_credit(
        self, account: Account, *, redeem_request_id: str | None = None
    ) -> tuple[ConsumeRateLimitResetCreditResponse, Account]:
        chatgpt_account_id = account.chatgpt_account_id
        if not chatgpt_account_id:
            raise AccountUsageResetConsumeUnavailableError("Account is missing ChatGPT account identity")
        effective_redeem_request_id = redeem_request_id or str(uuid4())
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        route = await self._resolve_usage_reset_credit_route(account, operation="usage_reset_credits_consume")
        try:
            response = await consume_rate_limit_reset_credit(
                access_token=access_token,
                account_id=chatgpt_account_id,
                redeem_request_id=effective_redeem_request_id,
                route=route,
                allow_direct_egress=route is None,
            )
            return (response, account)
        except UsageFetchError as exc:
            if exc.status_code != 401 or self._auth_manager is None:
                raise
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError as refresh_exc:
                raise AccountUsageResetConsumeUnavailableError(
                    f"Account credentials could not be refreshed: {refresh_exc.message}"
                ) from refresh_exc
            if not account.chatgpt_account_id:
                raise AccountUsageResetConsumeUnavailableError("Account is missing ChatGPT account identity") from exc
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            retry_route = await self._resolve_usage_reset_credit_route(account, operation="usage_reset_credits_consume")
            response = await consume_rate_limit_reset_credit(
                access_token=access_token,
                account_id=account.chatgpt_account_id,
                redeem_request_id=effective_redeem_request_id,
                route=retry_route,
                allow_direct_egress=retry_route is None,
            )
            return (response, account)

    async def _resolve_usage_reset_credit_route(
        self, account: Account, *, operation: str
    ) -> ResolvedUpstreamRoute | None:
        async with get_background_session() as session:
            return await resolve_upstream_route(
                session, account_id=account.id, operation=operation, scope="account", encryptor=self._encryptor
            )
