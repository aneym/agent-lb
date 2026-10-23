from __future__ import annotations

from app.core.anthropic.oauth import ANTHROPIC_PROVIDER_NAME
from app.core.providers.kimi import KIMI_PROVIDER_NAME

_KIMI_PLAN_REFUSAL_MARKERS = ("does not have access to", "plan supports only")
STOP_STATUS = {"mirror_token_unavailable": 503, "permission_error": 403}


def refresh_due(provider_name: str, status: int, account_id: str, retried_account_ids: set[str]) -> bool:
    return (
        status == 401
        and account_id not in retried_account_ids
        and provider_name in {ANTHROPIC_PROVIDER_NAME, KIMI_PROVIDER_NAME}
    )


def rejects_key(provider_name: str, status: int, error_code: str) -> bool:
    if provider_name == ANTHROPIC_PROVIDER_NAME:
        return False
    if provider_name == KIMI_PROVIDER_NAME:
        return status == 401 and error_code == "upstream_401"
    return True


def refuses_plan(provider_name: str, status: int, message: str) -> bool:
    lowered = message.casefold()
    return (
        provider_name == KIMI_PROVIDER_NAME
        and status == 403
        and any(marker in lowered for marker in _KIMI_PLAN_REFUSAL_MARKERS)
    )
