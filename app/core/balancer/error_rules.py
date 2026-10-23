from __future__ import annotations

import re
from typing import Any

from app.core.config.error_rule_schema import ErrorRule


def _kimi_rules() -> tuple[ErrorRule, ...]:
    matches = (
        ("suspended", None, "suspended"),
        ("account_suspended", None, "suspended"),
        (None, "suspended|unauthenticated:", "suspended"),
        ("insufficient_balance", None, "insufficient_balance"),
        (
            None,
            "insufficient balance|insufficient_balance|balance is insufficient|extra usage balance has run out",
            "insufficient_balance",
        ),
    )
    return tuple(
        ErrorRule(
            provider="kimi",
            http_status=status,
            error_code=code,
            message_regex=pattern,
            action="pause",
            reason=f"balance_exhausted: {reason}",
        )
        for status in (401, 402, 403, 429, 500)
        for code, pattern, reason in matches
    )


DEFAULT_ERROR_RULES = (
    ErrorRule(provider="glm", error_code="1113", action="pause", reason="balance_exhausted: 1113"),
    *_kimi_rules(),
)


def match_error_rule(
    provider: str,
    status: int,
    payload: Any,
    rules: tuple[ErrorRule, ...] = (),
) -> ErrorRule | None:
    if status < 400:
        return None
    body = payload if isinstance(payload, dict) else {}
    error = body.get("error", body)
    if not isinstance(error, dict):
        error = body
    code = str(error.get("code", error.get("type", body.get("code"))) or "").casefold()
    message = str(error.get("message", ""))
    for rule in (*rules, *DEFAULT_ERROR_RULES):
        if rule.provider != provider or (rule.http_status is not None and rule.http_status != status):
            continue
        if rule.error_code is not None and rule.error_code.casefold() != code:
            continue
        if rule.message_regex is not None and re.search(rule.message_regex, message, re.IGNORECASE) is None:
            continue
        return rule
    return None
