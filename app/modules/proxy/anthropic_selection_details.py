from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone

from app.core.providers import get_anthropic_compat_profile
from app.db.models import Account
from app.modules.proxy.load_balancer import selectable_accounts
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger(__name__)


class AnthropicSelectionDetails:
    _repo_factory: ProxyRepoFactory

    async def _selection_failure_details(
        self,
        *,
        model: str,
        quota_key: str,
        provider_name: str,
        fallback: str,
        quota_reset_at: int | None = None,
        quota_blocked_count: int = 0,
        quota_candidate_count: int | None = None,
        quota_cooldown_blocked_count: int = 0,
        quota_cooldown_reset_at: int | None = None,
        selection_error_code: str | None = None,
        selection_error_message: str | None = None,
        selection_retry_at: int | None = None,
        selection_cooldown_count: int = 0,
    ) -> tuple[str, int | None]:
        now = int(time.time())
        reset_candidates: list[int] = [quota_reset_at] if quota_reset_at is not None else []
        async with self._repo_factory() as repos:
            provider_accounts = [
                account for account in await repos.accounts.list_accounts() if account.provider.lower() == provider_name
            ]

            accounts = selectable_accounts(provider_accounts)
            unusable_count = len(provider_accounts) - len(accounts)
            account_count = len(accounts)
            status_counts = Counter(_account_status_label(account) for account in accounts)
            reset_candidates.extend(int(account.reset_at) for account in accounts if account.reset_at)
            primary_usage = await repos.usage.latest_by_account(
                "primary",
                account_ids=[account.id for account in accounts],
            )
            reset_candidates.extend(
                int(entry.reset_at)
                for entry in primary_usage.values()
                if entry.reset_at and float(entry.used_percent) >= 100.0
            )

        live_retries = [
            candidate
            for candidate in (quota_reset_at, quota_cooldown_reset_at, selection_retry_at)
            if candidate is not None and candidate > now
        ]
        if live_retries:
            retry_at = min(live_retries)
        else:
            future_resets = [candidate for candidate in reset_candidates if candidate > now]
            retry_at = min(future_resets) if future_resets else None
        if account_count == 0:
            return fallback, retry_at
        status_summary = ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        noun = "account" if account_count == 1 else "accounts"
        reset_suffix = (
            f" Limits reset at {datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()}."
            if retry_at is not None
            else ""
        )
        quota_detail = _anthropic_selection_quota_detail(
            quota_key=quota_key,
            blocked_count=quota_blocked_count,
            candidate_count=quota_candidate_count,
            cooldown_blocked_count=quota_cooldown_blocked_count,
            cooldown_reset_at=quota_cooldown_reset_at,
            selection_error_code=selection_error_code,
            selection_error_message=selection_error_message,
        )
        quota_sentence = f"Model quota: {quota_detail}. " if quota_detail else ""
        runtime_sentence = (
            f"Selector runtime cooldown excluded {selection_cooldown_count} "
            f"{'account' if selection_cooldown_count == 1 else 'accounts'}. "
            if selection_cooldown_count
            else ""
        )
        stored_note = (
            f" (+{unusable_count} stored but not routable: canceled, deactivated, paused, or reauth-required)"
            if unusable_count
            else ""
        )
        message = (
            f"{account_count} {get_anthropic_compat_profile(provider_name).label} {noun} exist, but none are selectable for "
            f"{model}/{quota_key}; statuses: {status_summary}.{stored_note} "
            f"{quota_sentence}{runtime_sentence}"
            f"{get_anthropic_compat_profile(provider_name).other_provider_routing_message}{reset_suffix}"
        )
        logger.warning(
            (
                "Anthropic account selection failed model=%s quota_key=%s statuses=%s "
                "quota_blocked=%s quota_cooldown_blocked=%s quota_candidates=%s "
                "selection_error_code=%s selection_error_message=%s runtime_cooldown_excluded=%s retry_at=%s"
            ),
            model,
            quota_key,
            status_summary,
            quota_blocked_count,
            quota_cooldown_blocked_count,
            quota_candidate_count,
            selection_error_code,
            selection_error_message,
            selection_cooldown_count,
            retry_at,
        )
        return message, retry_at


def _account_status_label(account: Account) -> str:
    status = account.status
    return status.value if hasattr(status, "value") else str(status)


def _anthropic_selection_quota_detail(
    *,
    quota_key: str,
    blocked_count: int,
    candidate_count: int | None,
    cooldown_blocked_count: int,
    cooldown_reset_at: int | None,
    selection_error_code: str | None,
    selection_error_message: str | None,
) -> str | None:
    parts: list[str] = []

    if cooldown_blocked_count > 0:
        noun = "account" if cooldown_blocked_count == 1 else "accounts"
        reset_text = (
            f" until {datetime.fromtimestamp(cooldown_reset_at).isoformat()}" if cooldown_reset_at is not None else ""
        )
        parts.append(f"{quota_key} cooldown excluded {cooldown_blocked_count} {noun}{reset_text}")
    other_blocked_count = max(0, blocked_count - cooldown_blocked_count)
    if other_blocked_count > 0:
        noun = "account" if other_blocked_count == 1 else "accounts"
        parts.append(f"{other_blocked_count} {noun} out of window or capped")
    if candidate_count is not None and (blocked_count > 0 or selection_error_code):
        noun = "account" if candidate_count == 1 else "accounts"
        parts.append(f"{candidate_count} {noun} remained after the {quota_key} prefilter")
    if selection_error_code or selection_error_message and selection_error_message != "No available accounts":
        reason = selection_error_code or selection_error_message
        if selection_error_code and selection_error_message and selection_error_message != "No available accounts":
            reason = f"{selection_error_message} ({selection_error_code})"
        parts.append(f"selector reason: {reason}")
    return "; ".join(parts) if parts else None
