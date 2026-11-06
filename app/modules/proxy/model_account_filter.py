from __future__ import annotations

from app.core.openai.model_registry import get_model_registry
from app.core.plan_types import account_plan_matches_allowed
from app.core.providers import OPENAI_PROVIDER_NAME, normalize_provider_name
from app.db.models import Account


def filter_accounts_for_model(accounts: list[Account], model: str) -> list[Account]:
    allowed_plans = get_model_registry().plan_types_for_model(model)
    if allowed_plans is None:
        return accounts
    return [
        account
        for account in accounts
        if normalize_provider_name(account.provider) != OPENAI_PROVIDER_NAME
        or account_plan_matches_allowed(account.plan_type, allowed_plans)
    ]
