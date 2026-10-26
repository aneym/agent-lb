from __future__ import annotations

from typing import cast

from app.core import usage as usage_core
from app.core.auth.refresh import RefreshError
from app.core.clients.usage import UsageFetchError
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.usage.models import UsagePayload
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow
from app.db.models import Account
from app.modules.accounts import reset_credit_cache
from app.modules.accounts.subscription_status import ANTHROPIC_OAUTH_ORGANIZATION_BLOCK
from app.modules.usage.account_sync import UsageAccountSync
from app.modules.usage.identity_mismatch import clear_identity_mismatch, record_identity_mismatch
from app.modules.usage.rate_limit_backoff import mark_usage_refresh_auth_cooldown
from app.modules.usage.refresh_ports import AccountRefreshResult, AccountsRepositoryWithStatusComparePort
from app.modules.usage.refresh_rules import (
    ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY,
    ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW,
    add_additional_usage_entry,
    bounded_exhaustion_reset_at,
    credits_snapshot,
    delete_additional_usage_quota_key,
    delete_additional_usage_quota_key_window,
    epoch_now,
    fetch_usage_payload,
    list_additional_usage_quota_keys,
    logger,
    merge_additional_rate_limits,
    minutes_of,
    payload_mismatches_account_slot,
    reset_time,
    should_deactivate_for_usage_error,
    usage_entry_written,
)


class UsageAccountRefresh(UsageAccountSync):
    async def _refresh_account(
        self,
        account: Account,
        *,
        usage_account_id: str | None,
    ) -> AccountRefreshResult:
        refresh_started_at = utcnow()
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        payload: UsagePayload | None = None
        try:
            payload = await fetch_usage_payload(
                account=account,
                access_token=access_token,
                usage_account_id=usage_account_id,
            )
        except UpstreamProxyRouteError as exc:
            logger.warning(
                "Usage refresh upstream proxy route unavailable account_id=%s reason=%s",
                account.id,
                exc.reason,
            )
            mark_usage_refresh_auth_cooldown(account.id, 0)
            return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
        except UsageFetchError as exc:
            if account.provider != "kimi" and should_deactivate_for_usage_error(exc):
                await self._deactivate_for_client_error(account, exc)
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
            if exc.status_code != 401 or not self._auth_manager:
                mark_usage_refresh_auth_cooldown(account.id, exc.status_code, exc.retry_after_seconds)
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
            try:
                account = await self._auth_manager.ensure_fresh(account, force=True)
            except RefreshError:
                mark_usage_refresh_auth_cooldown(account.id, exc.status_code)
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            try:
                payload = await fetch_usage_payload(
                    account=account,
                    access_token=access_token,
                    usage_account_id=usage_account_id,
                )
            except UpstreamProxyRouteError as route_exc:
                logger.warning(
                    "Usage refresh retry upstream proxy route unavailable account_id=%s reason=%s",
                    account.id,
                    route_exc.reason,
                )
                mark_usage_refresh_auth_cooldown(account.id, 0)
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
            except UsageFetchError as retry_exc:
                if account.provider != "kimi" and should_deactivate_for_usage_error(retry_exc):
                    await self._deactivate_for_client_error(account, retry_exc)
                else:
                    mark_usage_refresh_auth_cooldown(account.id, retry_exc.status_code, retry_exc.retry_after_seconds)
                return AccountRefreshResult(usage_written=False, fetch_succeeded=False)

        if payload is None:
            return AccountRefreshResult(usage_written=False, fetch_succeeded=False)

        if account.provider == ANTHROPIC_PROVIDER_NAME:
            for window in ("primary", "secondary"):
                latest = await self._usage_repo.latest_entry_for_account(account.id, window=window)
                if latest is not None and latest.recorded_at > refresh_started_at:
                    return AccountRefreshResult(usage_written=False, fetch_succeeded=True)

        if payload_mismatches_account_slot(account, payload):
            logger.warning(
                "Usage refresh payload identity mismatch; skipping account mutation "
                "account_id=%s stored_workspace_id=%s payload_workspace_id=%s stored_plan_type=%s "
                "payload_plan_type=%s stored_seat_type=%s payload_seat_type=%s request_id=%s",
                account.id,
                account.workspace_id,
                payload.workspace_id,
                account.plan_type,
                payload.plan_type,
                account.seat_type,
                payload.seat_type,
                get_request_id(),
            )
            record_identity_mismatch(
                account.id,
                stored_plan_type=account.plan_type,
                payload_plan_type=payload.plan_type,
                stored_workspace_id=account.workspace_id,
                payload_workspace_id=payload.workspace_id,
            )
            return AccountRefreshResult(usage_written=False, fetch_succeeded=False)
        clear_identity_mismatch(account.id)

        identity_matches_slot = await self._sync_identity_metadata(account, payload)
        if not identity_matches_slot:
            logger.warning(
                "Usage refresh payload reported a workspace slot owned by another account; "
                "skipping account usage mutation account_id=%s payload_workspace_id=%s request_id=%s",
                account.id,
                payload.workspace_id,
                get_request_id(),
            )
            return AccountRefreshResult(usage_written=False, fetch_succeeded=False)

        if account.provider == ANTHROPIC_PROVIDER_NAME:
            if payload.reset_credits_available is None:
                reset_credit_cache.clear(account.id)
            else:
                reset_credit_cache.record_count(account.id, payload.reset_credits_available)
        elif account.provider == OPENAI_PROVIDER_NAME:
            reset_summary = payload.rate_limit_reset_credits
            if reset_summary is not None and reset_summary.available_count is not None:
                reset_credit_cache.record_count(account.id, reset_summary.available_count)
        now_epoch = epoch_now()
        if self._additional_usage_repo is not None:
            if payload.additional_rate_limits:
                merged_limits = merge_additional_rate_limits(
                    payload.additional_rate_limits,
                    account_id=account.id,
                    now_epoch=now_epoch,
                )
                current_entries: set[tuple[str, str]] = set()
                for quota_key, windows in merged_limits.items():
                    for window, merged_window in windows.items():
                        current_entries.add((quota_key, window))
                        await add_additional_usage_entry(
                            self._additional_usage_repo,
                            account_id=account.id,
                            limit_name=merged_window.limit_name,
                            metered_feature=merged_window.metered_feature,
                            quota_key=quota_key,
                            window=window,
                            used_percent=merged_window.used_percent,
                            reset_at=merged_window.reset_at,
                            window_minutes=merged_window.window_minutes,
                        )
                current_quota_keys = {name for name, _ in current_entries}
                existing_quota_keys = await list_additional_usage_quota_keys(
                    self._additional_usage_repo,
                    account_ids=[account.id],
                )
                for stale_key in existing_quota_keys:
                    if stale_key not in current_quota_keys:
                        await delete_additional_usage_quota_key(
                            self._additional_usage_repo,
                            account.id,
                            stale_key,
                        )
                        continue
                    for window in ("primary", "secondary"):
                        if (stale_key, window) not in current_entries:
                            await delete_additional_usage_quota_key_window(
                                self._additional_usage_repo,
                                account.id,
                                stale_key,
                                window,
                            )
            elif payload.additional_rate_limits is not None:
                await self._additional_usage_repo.delete_for_account(account.id)

        scoped = payload.fable_scoped_weekly
        fable_scoped_written = False
        if (
            self._additional_usage_repo is not None
            and account.provider == ANTHROPIC_PROVIDER_NAME
            and scoped is not None
            and scoped.used_percent is not None
        ):
            await add_additional_usage_entry(
                self._additional_usage_repo,
                account_id=account.id,
                limit_name=ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY,
                metered_feature=ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY,
                quota_key=ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY,
                window=ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW,
                used_percent=float(scoped.used_percent),
                reset_at=reset_time(scoped.reset_at, scoped.reset_after_seconds, now_epoch),
                window_minutes=minutes_of(scoped.limit_window_seconds),
            )
            fable_scoped_written = True

        rate_limit = payload.rate_limit
        if rate_limit is None:
            additional_synced = (
                self._additional_usage_repo is not None and payload.additional_rate_limits is not None
            ) or fable_scoped_written
            return AccountRefreshResult(usage_written=additional_synced)

        normalized_windows = usage_core.normalize_rate_limit_windows(
            rate_limit.primary_window,
            rate_limit.secondary_window,
        )
        primary = normalized_windows.primary
        secondary = normalized_windows.secondary
        monthly = rate_limit.monthly_window or normalized_windows.monthly
        if primary is None and secondary is None:
            if monthly is None:
                additional_synced = (
                    self._additional_usage_repo is not None and payload.additional_rate_limits is not None
                ) or fable_scoped_written
                return AccountRefreshResult(usage_written=additional_synced)
        if primary is None and secondary is None and monthly is None:
            additional_synced = (
                self._additional_usage_repo is not None and payload.additional_rate_limits is not None
            ) or fable_scoped_written
            return AccountRefreshResult(usage_written=additional_synced)
        credits_has, credits_unlimited, credits_balance = credits_snapshot(payload)
        usage_written = fable_scoped_written

        if primary and primary.used_percent is not None:
            entry = await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(primary.used_percent),
                provider=account.provider,
                input_tokens=None,
                output_tokens=None,
                window="primary",
                recorded_at=refresh_started_at,
                reset_at=bounded_exhaustion_reset_at(
                    primary.used_percent,
                    primary.reset_at,
                    primary.reset_after_seconds,
                    primary.limit_window_seconds,
                    now_epoch,
                ),
                window_minutes=minutes_of(primary.limit_window_seconds),
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )
            usage_written = usage_written or usage_entry_written(entry)

        if secondary and secondary.used_percent is not None:
            entry = await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(secondary.used_percent),
                provider=account.provider,
                input_tokens=None,
                output_tokens=None,
                window="secondary",
                recorded_at=refresh_started_at,
                reset_at=reset_time(secondary.reset_at, secondary.reset_after_seconds, now_epoch),
                window_minutes=minutes_of(secondary.limit_window_seconds),
            )
            usage_written = usage_written or usage_entry_written(entry)

        if monthly and monthly.used_percent is not None:
            entry = await self._usage_repo.add_entry(
                account_id=account.id,
                used_percent=float(monthly.used_percent),
                provider=account.provider,
                input_tokens=None,
                output_tokens=None,
                window="monthly",
                reset_at=reset_time(monthly.reset_at, monthly.reset_after_seconds, now_epoch),
                window_minutes=minutes_of(monthly.limit_window_seconds),
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )
            usage_written = usage_written or usage_entry_written(entry)
        await self._recover_quota_status_from_usage(account, primary=primary, secondary=secondary, monthly=monthly)
        if self._auth_manager and account.deactivation_reason == ANTHROPIC_OAUTH_ORGANIZATION_BLOCK:
            repo = cast(AccountsRepositoryWithStatusComparePort, self._auth_manager._repo)
            updated = await repo.update_status_if_current(
                account.id,
                account.status,
                None,
                account.reset_at,
                blocked_at=account.blocked_at,
                expected_status=account.status,
                expected_deactivation_reason=ANTHROPIC_OAUTH_ORGANIZATION_BLOCK,
                expected_reset_at=account.reset_at,
                expected_blocked_at=account.blocked_at,
            )
            if updated:
                account.deactivation_reason = None
        return AccountRefreshResult(usage_written=usage_written)
