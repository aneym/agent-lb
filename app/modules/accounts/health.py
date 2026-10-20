from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.core.browser_profiles import profile_for
from app.core.config.settings import get_settings
from app.core.subscriptions import subscription_for
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.schemas import AccountProblem


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def account_problems(
    account: Account, primary: UsageHistory | None, secondary: UsageHistory | None
) -> list[AccountProblem]:
    now = datetime.now(UTC)
    problems: list[AccountProblem] = []
    if (
        account.owner_instance
        and account.owner_instance != get_settings().local_instance_id
        and account.access_expires_at
        and now - _utc(account.access_expires_at) > timedelta(hours=1)
    ):
        problems.append(
            AccountProblem(
                code="owner_stale",
                message=f"Owner {account.owner_instance} stopped pushing; token expired {_utc(account.access_expires_at).isoformat()}.",
            )
        )
    if account.status in {AccountStatus.QUOTA_EXCEEDED, AccountStatus.RATE_LIMITED}:
        reset = account.reset_at
        if reset is None:
            windows = [entry for entry in (secondary, primary) if entry is not None]
            exhausted = [entry for entry in windows if entry.used_percent is not None and entry.used_percent >= 100]
            governing = exhausted or windows
            reset = next((entry.reset_at for entry in governing if entry.reset_at is not None), None)
        if reset is not None and now.timestamp() - reset > 3600:
            problems.append(
                AccountProblem(
                    code="reset_missed",
                    message=f"Account remains {account.status.value} after reset {datetime.fromtimestamp(reset, UTC).isoformat()}.",
                )
            )
    observations = [_utc(entry.recorded_at) for entry in (primary, secondary) if entry and entry.recorded_at]
    if account.status in {AccountStatus.ACTIVE, AccountStatus.QUOTA_EXCEEDED} and observations:
        latest = max(observations)
        if now - latest > timedelta(hours=24):
            problems.append(
                AccountProblem(code="usage_stale", message=f"Last usage observation was {latest.isoformat()}.")
            )
    if account.status == AccountStatus.REAUTH_REQUIRED:
        profile = profile_for(account.email)
        command = (
            f"agent-lb reauth {account.email} (profile {profile.dir})"
            if profile
            else f"agent-lb profiles add {account.email} --dir <chrome dir>, then agent-lb reauth {account.email}"
        )
        problems.append(
            AccountProblem(
                code="reauth_required",
                message=f"Reauthentication required: {account.deactivation_reason or 'no reason recorded'}. Run: {command}.",
            )
        )
    subscription = subscription_for(account.email)
    if subscription:
        days = (date.fromisoformat(subscription.renews_on) - now.date()).days
        if subscription.payment != "card" and days <= 7:
            problems.append(
                AccountProblem(
                    code="renewal_payment",
                    message=f"Subscription {subscription.plan} renews {subscription.renews_on} on a {subscription.payment} payment method; put a real card on the account before then, then run: agent-lb subscriptions set {account.email} --payment card --renews-on <next date>.",
                )
            )
        elif subscription.payment == "card" and days < -2:
            problems.append(
                AccountProblem(
                    code="renewal_stale",
                    message=f"Renewal date {subscription.renews_on} passed; confirm the renewal and update: agent-lb subscriptions set {account.email} --renews-on <next date>.",
                )
            )
    return problems
