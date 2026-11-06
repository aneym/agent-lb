from __future__ import annotations

from app.core.anthropic.oauth import ANTHROPIC_PROVIDER_NAME
from app.core.providers.anthropic_compat import get_anthropic_compat_profile
from app.core.providers.kimi import KIMI_PROVIDER_NAME
from app.core.resilience.overload import is_local_overload_error_code
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError

_KIMI_PLAN_REFUSAL_MARKERS = ("does not have access to", "plan supports only")
STOP_STATUS = {"permission_error": 403}
_POOL_EXHAUSTED_CODES = frozenset({"no_accounts", "no_accounts_available", "no_eligible_accounts"})


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


def is_account_neutral_error_code(code: str | None) -> bool:
    return is_local_overload_error_code(code) or code in {
        "proxy_unavailable",
        "bridge_first_event_timeout",
    }


def pool_exhausted(exc: AnthropicProxyError, provider_name: str) -> bool:
    no_accounts_code = get_anthropic_compat_profile(provider_name).no_available_accounts_code
    return exc.code in _POOL_EXHAUSTED_CODES or exc.code == no_accounts_code
