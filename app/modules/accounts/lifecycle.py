from __future__ import annotations

from datetime import datetime

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.config.settings import get_settings
from app.core.providers import (
    ANTHROPIC_COMPAT_PROFILES,
    ANTHROPIC_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    normalize_provider_name,
)
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import AccountStatus
from app.modules.accounts import probes
from app.modules.accounts.errors import AccountNotProbableError, AccountStateTransitionError
from app.modules.accounts.schemas import (
    AccountProbeResponse,
    AccountSubscriptionCheckResponse,
    AccountSubscriptionLedger,
)
from app.modules.accounts.subscription_status import (
    CANCELED_SUBSCRIPTION_STATUS,
    normalize_subscription_status,
)
from app.modules.accounts.transfer import AccountsTransfer
from app.modules.proxy.account_cache import get_account_selection_cache


class AccountsLifecycle(AccountsTransfer):
    async def reactivate_account(self, account_id: str) -> bool:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return False
        if account.status == AccountStatus.REAUTH_REQUIRED:
            raise AccountStateTransitionError("Account requires re-authentication and cannot be reactivated directly")
        if account.status == AccountStatus.EXCHANGE_UNCERTAIN:
            raise AccountStateTransitionError("Refresh exchange is unresolved; reset and re-login before reactivation")
        result = await self._repo.update_status_if_current(
            account_id,
            AccountStatus.ACTIVE,
            None,
            None,
            blocked_at=None,
            expected_status=account.status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not result:
            raise AccountStateTransitionError("Account state changed; retry the operation")
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def pause_account(self, account_id: str, *, reason: str | None = None) -> bool:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return False
        if account.status in (
            AccountStatus.REAUTH_REQUIRED,
            AccountStatus.DEACTIVATED,
            AccountStatus.EXCHANGE_UNCERTAIN,
        ):
            raise AccountStateTransitionError(f"Account is {account.status.value} and cannot be paused")
        result = await self._repo.update_status_if_current(
            account_id,
            AccountStatus.PAUSED,
            reason,
            None,
            blocked_at=None,
            expected_status=account.status,
            expected_deactivation_reason=account.deactivation_reason,
            expected_reset_at=account.reset_at,
            expected_blocked_at=account.blocked_at,
        )
        if not result:
            current = await self._repo.reload_by_id(account_id)
            if (
                reason is not None
                and reason.startswith("balance_exhausted:")
                and current is not None
                and current.status == AccountStatus.PAUSED
                and (current.deactivation_reason or "").startswith("balance_exhausted:")
            ):
                get_account_selection_cache().invalidate()
                return True
            raise AccountStateTransitionError("Account state changed; retry the operation")
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def update_account(self, account_id: str, *, security_work_authorized: bool | None = None) -> bool:
        result = False
        if security_work_authorized is not None:
            result = await self._repo.update_security_work_authorized(account_id, security_work_authorized)
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def set_limit_warmup_enabled(self, account_id: str, enabled: bool) -> bool:
        result = await self._repo.update_limit_warmup_enabled(account_id, enabled)
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def set_routing_policy(self, account_id: str, routing_policy: str) -> bool:
        result = await self._repo.update_routing_policy(account_id, routing_policy)
        if result:
            get_account_selection_cache().invalidate()
        return result

    async def set_subscription_ledger(
        self,
        account_id: str,
        payload: AccountSubscriptionLedger,
    ) -> AccountSubscriptionLedger | None:
        currency = payload.currency.upper() if payload.currency else None
        notes = payload.notes.strip() if payload.notes else None
        if notes == "":
            notes = None
        next_charge_at = to_utc_naive(payload.next_charge_at) if payload.next_charge_at else None
        current_period_end_at = to_utc_naive(payload.current_period_end_at) if payload.current_period_end_at else None
        last_verified_at = to_utc_naive(payload.last_verified_at) if payload.last_verified_at else None
        updated = await self._repo.update_subscription_ledger(
            account_id,
            status=payload.status,
            next_charge_at=next_charge_at,
            current_period_end_at=current_period_end_at,
            amount=payload.amount,
            currency=currency,
            last_verified_at=last_verified_at,
            notes=notes,
        )
        if not updated:
            return None
        get_account_selection_cache().invalidate()
        return AccountSubscriptionLedger(
            status=payload.status,
            next_charge_at=next_charge_at,
            current_period_end_at=current_period_end_at,
            amount=payload.amount,
            currency=currency,
            last_verified_at=last_verified_at,
            notes=notes,
        )

    async def check_subscription(self, account_id: str) -> AccountSubscriptionCheckResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        if normalize_subscription_status(account.subscription_status) != CANCELED_SUBSCRIPTION_STATUS:
            raise AccountStateTransitionError("Subscription checks are only available for canceled accounts")

        check_account = account
        if self._auth_manager is not None:
            check_account = await self._auth_manager.ensure_fresh(account, force=False)

        access_token = self._encryptor.decrypt(check_account.access_token_encrypted)
        provider = normalize_provider_name(check_account.provider)
        message: str | None = None
        if provider == ANTHROPIC_PROVIDER_NAME:
            probe_status, message = await self._send_anthropic_subscription_check_request(access_token=access_token)
        elif provider == OPENAI_PROVIDER_NAME:
            probe_status = await self._send_probe_request(
                access_token=access_token,
                chatgpt_account_id=check_account.chatgpt_account_id,
                model=probes.DEFAULT_PROBE_MODEL,
            )
        else:
            raise AccountStateTransitionError(f"Provider {provider} cannot be subscription-checked")

        working = 200 <= probe_status < 300
        now = utcnow()
        notes = _subscription_check_notes(
            working=working,
            probe_status=probe_status,
            checked_at=now,
            message=message,
        )
        subscription = await self.set_subscription_ledger(
            account_id,
            AccountSubscriptionLedger(
                status="active" if working else "canceled",
                next_charge_at=account.subscription_next_charge_at,
                current_period_end_at=account.subscription_current_period_end_at,
                amount=account.subscription_amount,
                currency=account.subscription_currency,
                last_verified_at=now,
                notes=notes,
            ),
        )
        return AccountSubscriptionCheckResponse(
            status="checked",
            account_id=account_id,
            working=working,
            probe_status_code=probe_status,
            subscription=subscription,
            message=message,
        )

    async def delete_account(self, account_id: str, *, delete_history: bool = False) -> bool:
        result = await self._repo.delete(account_id, delete_history=delete_history)
        if result:
            get_account_selection_cache().invalidate()
            get_api_key_cache().clear()
            poller = get_cache_invalidation_poller()
            if poller is not None:
                await poller.bump(NAMESPACE_API_KEY)
        return result

    async def set_account_alias(self, account_id: str, alias: str | None) -> bool:
        normalized = alias.strip() if isinstance(alias, str) else None
        if normalized == "":
            normalized = None
        return await self._repo.update_alias(account_id, normalized)

    async def probe_account(
        self,
        account_id: str,
        model: str | None = None,
    ) -> AccountProbeResponse | None:

        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        if account.status in (
            AccountStatus.PAUSED,
            AccountStatus.REAUTH_REQUIRED,
            AccountStatus.DEACTIVATED,
            AccountStatus.EXCHANGE_UNCERTAIN,
        ):
            raise AccountNotProbableError(f"Account is {account.status.value} and cannot be probed")

        primary_before, secondary_before = await self._latest_usage_percents(account_id)
        status_before = account.status.value

        probe_account = account
        if self._auth_manager is not None:
            probe_account = await self._auth_manager.ensure_fresh(account, force=False)

        access_token = self._encryptor.decrypt(probe_account.access_token_encrypted)
        probe_model = model or probes.DEFAULT_PROBE_MODEL
        provider = normalize_provider_name(probe_account.provider)
        compat_profiles = {profile.provider_name: profile for profile in ANTHROPIC_COMPAT_PROFILES}
        compat_profile = compat_profiles.get(provider)
        if compat_profile is not None:
            base_url = compat_profile.upstream_base_url
            if base_url is None:
                assert compat_profile.upstream_settings_attr is not None
                base_url = str(getattr(get_settings(), compat_profile.upstream_settings_attr))
            probe_status, _ = await self._send_messages_probe_request(
                access_token=access_token,
                base_url=base_url,
                model=model or compat_profile.default_probe_model,
            )
        else:
            probe_status = await self._send_probe_request(
                access_token=access_token,
                chatgpt_account_id=probe_account.chatgpt_account_id,
                model=probe_model,
            )

        if self._usage_repo and self._usage_updater and provider in {ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME}:
            await self._usage_updater.force_refresh(probe_account)
            get_account_selection_cache().invalidate()

        refreshed = await self._repo.get_by_id(account_id) or account
        primary_after, secondary_after = await self._latest_usage_percents(account_id)

        return AccountProbeResponse(
            status="probed",
            account_id=account_id,
            probe_status_code=probe_status,
            primary_used_percent_before=primary_before,
            primary_used_percent_after=primary_after,
            secondary_used_percent_before=secondary_before,
            secondary_used_percent_after=secondary_after,
            account_status_before=status_before,
            account_status_after=refreshed.status.value,
        )

    async def _latest_usage_percents(self, account_id: str) -> tuple[float | None, float | None]:
        if self._usage_repo is None:
            return None, None
        primary_entry = await self._usage_repo.latest_entry_for_account(account_id, window="primary")
        secondary_entry = await self._usage_repo.latest_entry_for_account(account_id, window="secondary")
        return (
            primary_entry.used_percent if primary_entry is not None else None,
            secondary_entry.used_percent if secondary_entry is not None else None,
        )

    async def _send_probe_request(
        self,
        *,
        access_token: str,
        chatgpt_account_id: str | None,
        model: str,
    ) -> int:
        return await probes.send_openai_probe(
            access_token=access_token,
            chatgpt_account_id=chatgpt_account_id,
            model=model,
        )

    async def _send_messages_probe_request(
        self,
        *,
        access_token: str,
        base_url: str,
        model: str,
    ) -> tuple[int, str | None]:
        return await probes.send_messages_probe(
            access_token=access_token,
            base_url=base_url,
            model=model,
        )

    async def _send_anthropic_subscription_check_request(
        self,
        *,
        access_token: str,
    ) -> tuple[int, str | None]:
        return await self._send_messages_probe_request(
            access_token=access_token,
            base_url=get_settings().anthropic_upstream_base_url,
            model=probes.DEFAULT_ANTHROPIC_SUBSCRIPTION_CHECK_MODEL,
        )


def _subscription_check_notes(
    *,
    working: bool,
    probe_status: int,
    checked_at: datetime,
    message: str | None,
) -> str:
    date = checked_at.date().isoformat()
    if working:
        return f"Subscription check succeeded on {date}; account is locally active."
    suffix = f" {message}" if message else ""
    return (
        f"Subscription check returned HTTP {probe_status} on {date}; "
        f"keeping account canceled until reactivated.{suffix}"
    )
