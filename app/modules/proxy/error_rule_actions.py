from __future__ import annotations

from app.core.config.error_rule_schema import ErrorRule
from app.db.models import Account
from app.modules.accounts.service import AccountsService
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepoFactory


async def apply_error_rule(
    rule: ErrorRule,
    account: Account,
    repo_factory: ProxyRepoFactory,
    balancer: LoadBalancer,
) -> None:
    if rule.action == "pause":
        async with repo_factory() as repos:
            await AccountsService(repos.accounts).pause_account(account.id, reason=rule.reason)
    elif rule.action == "cooldown":
        await balancer.mark_rate_limit(account, {"resets_in_seconds": rule.cooldown_seconds})
        await balancer.cool_down(account, rule.cooldown_seconds)


def rule_error_code(rule: ErrorRule) -> str:
    return "balance_exhausted" if rule.reason.startswith("balance_exhausted: ") else "upstream_rule"


def rule_error_message(rule: ErrorRule) -> str:
    if rule_error_code(rule) == "balance_exhausted":
        return f"No balance ({rule.reason.removeprefix('balance_exhausted: ')})"
    return rule.reason


def rule_client_message(rule: ErrorRule, provider_label: str) -> str:
    if rule_error_code(rule) == "balance_exhausted":
        return f"No balance for {provider_label} ({rule.reason.removeprefix('balance_exhausted: ')})"
    return rule.reason
