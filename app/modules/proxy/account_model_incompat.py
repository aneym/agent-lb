"""Per-(account, model) memory of upstream "model not supported" rejections.

ChatGPT answers a Codex request for a model the account's current plan does not
include with a 400 ``invalid_request_error``:

    The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT account.

The account itself is healthy (other models keep working), so this must not
mark the account unhealthy. It must also not surface to the client while
another account can serve the model: selection skips the (account, model)
pair until the TTL lapses, and every transport fails the request over.
"""

from __future__ import annotations

import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

ACCOUNT_MODEL_UNSUPPORTED_CODE = "account_model_unsupported"
DEFAULT_TTL_SECONDS = 1800.0
_MESSAGE_HINTS = ("model is not supported when using codex with a chatgpt account",)
_MODEL_IN_MESSAGE = re.compile(r"the '([^']+)' model is not supported", re.IGNORECASE)


def is_account_model_unsupported_error(code: str | None, message: str | None) -> bool:
    if (code or "").strip().lower() == ACCOUNT_MODEL_UNSUPPORTED_CODE:
        return True
    normalized = " ".join((message or "").strip().lower().split())
    return bool(normalized) and any(hint in normalized for hint in _MESSAGE_HINTS)


def unsupported_model_from_message(message: str | None) -> str | None:
    match = _MODEL_IN_MESSAGE.search(message or "")
    return match.group(1).strip().lower() if match else None


class AccountModelIncompatibility:
    def __init__(self) -> None:
        self._until: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def mark(self, account_id: str, model: str | None, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        if not account_id or not model:
            return
        model = model.strip().lower()
        with self._lock:
            self._until[(account_id, model)] = time.monotonic() + ttl_seconds
        logger.warning(
            "account_model_unsupported marked account_id=%s model=%s ttl_seconds=%.0f",
            account_id,
            model,
            ttl_seconds,
        )

    def blocked_account_ids(self, model: str | None) -> frozenset[str]:
        if not model:
            return frozenset()
        model = model.strip().lower()
        now = time.monotonic()
        with self._lock:
            expired = [key for key, until in self._until.items() if until <= now]
            for key in expired:
                del self._until[key]
            return frozenset(account_id for (account_id, blocked_model) in self._until if blocked_model == model)

    def clear(self) -> None:
        with self._lock:
            self._until.clear()


_incompatibility = AccountModelIncompatibility()


def get_account_model_incompatibility() -> AccountModelIncompatibility:
    return _incompatibility
