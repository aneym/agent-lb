from __future__ import annotations

import math
import time
from collections.abc import Collection, Mapping
from datetime import datetime, timezone
from typing import Any

from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.resilience.overload import is_local_overload_error_code
from app.modules.proxy.selection_diagnostics import selection_error_extras

TERMINAL_AFTER_SECONDS = 300
POOL_EXHAUSTED_HEADER = "x-agent-lb-pool-exhausted"
USAGE_LIMIT_REACHED = "usage_limit_reached"
_ANTHROPIC_COMPAT_PROVIDERS = {
    kind: provider
    for provider in ("anthropic", "glm", "kimi", "openrouter")
    for kind in (f"no_available_{provider}_accounts", f"{provider}_quota_cooldown")
}
_QUOTA_LIMITED_STATUSES = frozenset({"rate_limited", "quota_exceeded"})
_QUOTA_BLOCKED_STATUSES = _QUOTA_LIMITED_STATUSES | {"reauth_required", "deactivated", "paused"}
POOL_EXHAUSTED_CODES = frozenset(
    {"no_accounts", "no_accounts_available", "no_eligible_accounts", "quota_exhausted", "no_gemini_account"}
    | _ANTHROPIC_COMPAT_PROVIDERS.keys()
)


def is_terminal(
    code: object, resets_at: float | None, *, statuses: Collection[object], now: float | None = None
) -> bool:
    if code not in POOL_EXHAUSTED_CODES or not statuses:
        return False
    if not set(statuses) <= _QUOTA_BLOCKED_STATUSES or not set(statuses) & _QUOTA_LIMITED_STATUSES:
        return False
    if resets_at is None:
        return True
    current = time.time() if now is None else now
    return resets_at - current > TERMINAL_AFTER_SECONDS


def pool_headers(provider: str, resets_at: float | None, *, now: float | None = None) -> dict[str, str]:
    headers = {POOL_EXHAUSTED_HEADER: provider, "x-should-retry": "false"}
    if resets_at is not None:
        current = time.time() if now is None else now
        headers["Retry-After"] = str(max(1, math.ceil(resets_at - current)))
    return headers


def anthropic_terminal_headers(code: str, retry_at: int | None) -> dict[str, str] | None:
    provider = _ANTHROPIC_COMPAT_PROVIDERS.get(code)
    resets_at = None if retry_at is None else float(retry_at)
    if provider is None or resets_at is None or not is_terminal(code, resets_at, statuses=("rate_limited",)):
        return None
    headers = pool_headers(provider, resets_at)
    headers.pop("Retry-After", None)
    return {**headers, "anthropic-ratelimit-unified-status": "rejected"}


def gemini_terminal(selection: Any, retry_at: float | None) -> tuple[str, dict[str, str]] | None:
    code = selection.error_code or "no_gemini_account"
    if not is_terminal(code, retry_at, statuses=[entry.status for entry in selection.excluded_accounts]):
        return None
    return exhausted_message("gemini", retry_at), pool_headers("gemini", retry_at)


def _reset_label(resets_at: float | None) -> str:
    if resets_at is None:
        return "unknown"
    return datetime.fromtimestamp(resets_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def exhausted_message(provider: str, resets_at: float | None) -> str:
    return f"agent-lb: every {provider} account in this pool is out of quota; earliest reset {_reset_label(resets_at)}."


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def openai_terminal(
    content: Mapping[str, Any], *, provider: str = "openai", now: float | None = None
) -> tuple[dict[str, Any], dict[str, str]] | None:
    error = content.get("error")
    if not isinstance(error, Mapping):
        return None
    diagnostics = error.get("diagnostics")
    accounts = diagnostics.get("accounts") if isinstance(diagnostics, Mapping) else None
    resets_at = _number(error.get("resets_at"))
    statuses = [account.get("status") for account in accounts or [] if isinstance(account, Mapping)]
    if not is_terminal(error.get("code"), resets_at, statuses=statuses, now=now):
        return None
    terminal = {
        **error,
        "type": USAGE_LIMIT_REACHED,
        "code": USAGE_LIMIT_REACHED,
        "pool_exhausted_code": error.get("code"),
        "message": exhausted_message(provider, resets_at),
    }
    return {**content, "error": terminal}, pool_headers(provider, resets_at, now=now)


def websocket_selection_failure(
    selection: Any, error_code: str, error_message: str, *, requested_model: str | None
) -> tuple[int, OpenAIErrorEnvelope]:
    status_code = 429 if is_local_overload_error_code(error_code) else 503
    payload = openai_error(
        error_code, error_message, error_type="rate_limit_error" if status_code == 429 else "server_error"
    )
    payload["error"].update(selection_error_extras(selection, requested_model=requested_model))  # type: ignore[typeddict-item]
    terminal = openai_terminal(payload)
    if terminal is None:
        return status_code, payload
    return 429, terminal[0]  # type: ignore[return-value]
