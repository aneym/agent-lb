from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core import usage as usage_core
from app.core.auth import DEFAULT_EMAIL, extract_id_token_claims, token_expiry_epoch_ms
from app.core.config import settings as config_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME
from app.core.usage.quota import apply_usage_quota
from app.core.utils.time import from_epoch_seconds, utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.db.request_models import AccountLimitWarmup
from app.modules.accounts.credits import OPENROUTER_CREDITS_QUOTA_KEY
from app.modules.accounts.schemas import AccountAdditionalQuota, AccountAuthStatus, AccountFableScopedWeekly, AccountIdentityMismatch, AccountLimitWarmupStatus, AccountSubscriptionLedger, AccountTokenStatus  # noqa: I001  # fmt: skip
from app.modules.accounts.subscription_status import is_subscription_usable, normalize_subscription_status
from app.modules.usage.additional_quota_keys import raw_model_ids_for_quota_key
from app.modules.usage.identity_mismatch import get_identity_mismatch
from app.modules.usage.mappers import usage_history_to_window_row

_ACCOUNT_ROUTING_POLICIES = frozenset({"burn_first", "normal", "preserve"})
_DEFAULT_USAGE_REFRESH_INTERVAL_SECONDS = 60
_FABLE_SCOPED_FRESH_SECONDS = 21600


def _duplicate_detection_keys_appearing_more_than_once(accounts: list[Account]) -> set[tuple[str, str, str | None]]:

    counts: dict[tuple[str, str, str | None], int] = {}
    for account in accounts:
        key = _duplicate_detection_key(account)
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def _duplicate_detection_key(account: Account) -> tuple[str, str, str | None] | None:
    email = account.email
    chatgpt_account_id = account.chatgpt_account_id
    if not _is_duplicate_detection_email(email) or not chatgpt_account_id:
        return None
    return email, chatgpt_account_id, account.workspace_id


def _is_duplicate_detection_email(email: str | None) -> bool:
    return bool(email and email.strip()) and email != DEFAULT_EMAIL


def _openrouter_additional_quotas(
    existing: list[AccountAdditionalQuota] | None,
) -> list[AccountAdditionalQuota]:
    model_ids = list(raw_model_ids_for_quota_key(OPENROUTER_CREDITS_QUOTA_KEY))
    quotas = list(existing or [])
    for index, quota in enumerate(quotas):
        if quota.quota_key == OPENROUTER_CREDITS_QUOTA_KEY:
            quotas[index] = quota.model_copy(update={"model_ids": model_ids})
            return quotas
    quotas.append(
        AccountAdditionalQuota(
            quota_key=OPENROUTER_CREDITS_QUOTA_KEY,
            limit_name=OPENROUTER_CREDITS_QUOTA_KEY,
            metered_feature="openrouter_completions",
            display_label="OpenRouter credits",
            routing_policy="normal",
            model_ids=model_ids,
        )
    )
    return quotas


def _identity_mismatch_status(account_id: str) -> AccountIdentityMismatch | None:
    entry = get_identity_mismatch(account_id)
    if entry is None:
        return None
    return AccountIdentityMismatch(
        count=entry["count"],
        first_at=entry["first_at"],
        last_at=entry["last_at"],
        stored_plan_type=entry.get("stored_plan_type"),
        payload_plan_type=entry.get("payload_plan_type"),
    )


def _account_subscription_ledger(account: Account) -> AccountSubscriptionLedger | None:
    if (
        account.subscription_status is None
        and account.subscription_next_charge_at is None
        and account.subscription_current_period_end_at is None
        and account.subscription_amount is None
        and account.subscription_currency is None
        and account.subscription_last_verified_at is None
        and account.subscription_notes is None
    ):
        return None
    return AccountSubscriptionLedger(
        status=normalize_subscription_status(account.subscription_status),
        next_charge_at=account.subscription_next_charge_at,
        current_period_end_at=account.subscription_current_period_end_at,
        amount=account.subscription_amount,
        currency=account.subscription_currency,
        last_verified_at=account.subscription_last_verified_at,
        notes=account.subscription_notes,
    )


def _normalize_account_routing_policy(value: str | None) -> str:
    if value in _ACCOUNT_ROUTING_POLICIES:
        return value
    return "normal"


def _fable_eligible(
    account: Account,
    secondary_used_percent: float | None,
    fable_scoped_weekly: AdditionalUsageHistory | None = None,
) -> bool | None:

    if account.provider != ANTHROPIC_PROVIDER_NAME:
        return None
    if account.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED):
        return False
    if not is_subscription_usable(account):
        return False
    settings = config_settings.get_settings()
    if fable_scoped_weekly is not None and _fable_scoped_weekly_is_fresh(fable_scoped_weekly.recorded_at):
        return float(fable_scoped_weekly.used_percent) < settings.anthropic_fable_scoped_max_used_percent
    threshold = settings.anthropic_fable_weekly_max_used_percent
    if secondary_used_percent is None:
        return True
    return float(secondary_used_percent) < threshold


def _fable_scoped_weekly_summary(
    account: Account,
    fable_scoped_weekly: AdditionalUsageHistory | None,
) -> AccountFableScopedWeekly | None:

    if account.provider != ANTHROPIC_PROVIDER_NAME or fable_scoped_weekly is None:
        return None
    return AccountFableScopedWeekly(
        used_percent=float(fable_scoped_weekly.used_percent),
        reset_at=from_epoch_seconds(fable_scoped_weekly.reset_at),
        recorded_at=fable_scoped_weekly.recorded_at,
        fresh=_fable_scoped_weekly_is_fresh(fable_scoped_weekly.recorded_at),
    )


def _fable_scoped_weekly_is_fresh(recorded_at: datetime | None) -> bool:
    if recorded_at is None:
        return False
    current_time = datetime.now(timezone.utc)
    recorded_time = recorded_at if recorded_at.tzinfo is not None else recorded_at.replace(tzinfo=timezone.utc)
    return recorded_time >= current_time - timedelta(seconds=_FABLE_SCOPED_FRESH_SECONDS)


def _limit_warmup_to_status(entry: AccountLimitWarmup | None) -> AccountLimitWarmupStatus | None:
    if entry is None:
        return None
    return AccountLimitWarmupStatus(
        window=entry.window,
        reset_at=entry.reset_at,
        status=entry.status,
        model=entry.model,
        attempted_at=entry.attempted_at,
        completed_at=entry.completed_at,
        error_code=entry.error_code,
        error_message=entry.error_message,
    )


def _effective_status_from_usage(
    account: Account,
    *,
    status_seed: AccountStatus,
    primary_usage: UsageHistory | None,
    primary_used_percent: float | None,
    secondary_usage: UsageHistory | None,
    secondary_used_percent: float | None,
    monthly_usage: UsageHistory | None,
    monthly_used_percent: float | None,
    runtime_reset: float | None,
    credits_has: bool | None = None,
    credits_unlimited: bool | None = None,
    credits_balance: float | None = None,
    allow_missing_runtime_reset_recovery: bool = False,
) -> AccountStatus:
    long_window_usage = monthly_usage or secondary_usage
    long_window_used_percent = monthly_used_percent if monthly_usage is not None else secondary_used_percent

    credit_override_enabled = account.provider != ANTHROPIC_PROVIDER_NAME
    if not credit_override_enabled:
        credits_has, credits_unlimited, credits_balance = None, None, None
    elif credits_has is None and credits_unlimited is None and credits_balance is None:
        credits_has, credits_unlimited, credits_balance = _extract_credit_status(
            primary_usage,
            long_window_usage,
        )
    status, _, _ = apply_usage_quota(
        status=status_seed,
        primary_used=primary_used_percent,
        primary_reset=primary_usage.reset_at if primary_usage is not None else None,
        primary_window_minutes=primary_usage.window_minutes if primary_usage is not None else None,
        runtime_reset=runtime_reset,
        secondary_used=long_window_used_percent,
        secondary_reset=long_window_usage.reset_at if long_window_usage is not None else None,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )
    if account.status == AccountStatus.RATE_LIMITED and status == AccountStatus.ACTIVE:
        if runtime_reset is None and allow_missing_runtime_reset_recovery:
            return status
        if _has_credit_override(
            credits_has=credits_has,
            credits_unlimited=credits_unlimited,
            credits_balance=credits_balance,
        ):
            return status
        if account.blocked_at is None and account.reset_at is not None and account.reset_at <= utcnow().timestamp():
            return status
        return account.status
    if account.status == AccountStatus.QUOTA_EXCEEDED:
        now = utcnow().timestamp()
        primary_exhausted = primary_used_percent is not None and primary_used_percent >= 100.0
        long_window_available = long_window_used_percent is not None and long_window_used_percent < 100.0
        runtime_reset_elapsed = runtime_reset is not None and runtime_reset <= now
        if runtime_reset_elapsed and long_window_available and not primary_exhausted:
            return AccountStatus.ACTIVE
    return status


def _has_credit_override(
    *,
    credits_has: bool | None,
    credits_unlimited: bool | None,
    credits_balance: float | None,
) -> bool:
    from app.core.usage.quota import _has_credit_override as quota_has_credit_override

    return quota_has_credit_override(
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )


def _first_not_none(primary_usage: UsageHistory | None, secondary_usage: UsageHistory | None, field: str):
    if primary_usage is not None:
        value = getattr(primary_usage, field)
        if value is not None:
            return value
    if secondary_usage is not None:
        return getattr(secondary_usage, field)
    return None


def _usage_entry_is_recent_enough(recorded_at: datetime | None) -> bool:
    if recorded_at is None:
        return False
    current_time = datetime.now(timezone.utc)
    interval_seconds = max(_usage_refresh_interval_seconds() * 2, 180)
    recorded_time = recorded_at if recorded_at.tzinfo is not None else recorded_at.replace(tzinfo=timezone.utc)
    return recorded_time >= current_time - timedelta(seconds=interval_seconds)


def _usage_refresh_interval_seconds() -> int:
    settings = config_settings.get_settings()
    return int(getattr(settings, "usage_refresh_interval_seconds", _DEFAULT_USAGE_REFRESH_INTERVAL_SECONDS))


def _effective_usage_windows(
    primary_usage: UsageHistory | None,
    secondary_usage: UsageHistory | None,
) -> tuple[UsageHistory | None, UsageHistory | None]:
    if primary_usage is None:
        return None, secondary_usage
    if not usage_core.is_weekly_window_minutes(primary_usage.window_minutes):
        return primary_usage, secondary_usage
    if secondary_usage is None:
        return None, primary_usage
    if usage_core.should_use_weekly_primary(
        usage_history_to_window_row(primary_usage), usage_history_to_window_row(secondary_usage)
    ):
        return None, primary_usage
    return None, secondary_usage


def _build_auth_status(account: Account, encryptor: TokenEncryptor) -> AccountAuthStatus:
    access_token = _decrypt_token(encryptor, account.access_token_encrypted)
    refresh_token = _decrypt_token(encryptor, account.refresh_token_encrypted)
    id_token = _decrypt_token(encryptor, account.id_token_encrypted)

    access_expires = _token_expiry(access_token)
    refresh_state = "stored" if refresh_token else "missing"
    id_state = "unknown"
    if id_token:
        claims = extract_id_token_claims(id_token)
        if claims.model_dump(exclude_none=True):
            id_state = "parsed"

    return AccountAuthStatus(
        access=AccountTokenStatus(expires_at=access_expires),
        refresh=AccountTokenStatus(state=refresh_state),
        id_token=AccountTokenStatus(state=id_state),
    )


def _decrypt_token(encryptor: TokenEncryptor, encrypted: bytes | None) -> str | None:
    if not encrypted:
        return None
    try:
        return encryptor.decrypt(encrypted)
    except Exception:
        return None


def _token_expiry(token: str | None) -> datetime | None:
    if not token:
        return None
    expires_ms = token_expiry_epoch_ms(token)
    if expires_ms is not None:
        return datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)
    return None


def _normalize_used_percent(entry: UsageHistory | None) -> float | None:
    if not entry:
        return None
    return entry.used_percent


def _extract_credit_status(
    *entries: UsageHistory | None,
) -> tuple[bool | None, bool | None, float | None]:
    credit_entries = [
        entry
        for entry in entries
        if entry is not None
        and not (entry.credits_has is None and entry.credits_unlimited is None and entry.credits_balance is None)
    ]
    if not credit_entries:
        return None, None, None
    entry = max(
        credit_entries,
        key=lambda item: item.recorded_at if item.recorded_at is not None else datetime.min,
    )
    return entry.credits_has, entry.credits_unlimited, entry.credits_balance
