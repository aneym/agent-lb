from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts.identity_values import _slot_lock_keys


@dataclass(frozen=True, slots=True)
class AccountRequestUsageSummary:
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


ACCOUNT_PENDING_DELETION_REASON = "pending_deletion"


def credentials_replaced_since_wipe(
    access_token_encrypted: bytes, refresh_token_encrypted: bytes, id_token_encrypted: bytes
) -> bool:
    encryptor = TokenEncryptor()
    for ciphertext in (access_token_encrypted, refresh_token_encrypted, id_token_encrypted):
        try:
            if encryptor.decrypt(ciphertext) != "":
                return True
        except Exception:
            return True
    return False


EXCHANGE_RESET_REASONS = frozenset({"operator_reset", "auto_reset"})


@dataclass(frozen=True)
class AutoResetResult:
    outcome: Literal["reset", "waiting", "exhausted", "ineligible", "unsaved"]
    attempt: int
    prior_replay: bool
    prior_reason: str | None


_UNSET = object()
_HARD_STICKY_UNAVAILABLE_STATUSES = frozenset(
    (AccountStatus.PAUSED, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)
)
_HARD_STICKY_OUTAGE_GRACE_SEEDED_SENTINEL = "hard_sticky_outage_grace_seeded"
_SUMMARY_CACHE_TTL_SECONDS = 30.0
_SUMMARY_CACHE_MAX_ENTRIES = 64
_request_usage_summary_cache: dict[tuple[str, ...] | None, tuple[dict[str, AccountRequestUsageSummary], float]] = {}
_summary_cache_generation = 0


def _clear_request_usage_summary_cache() -> None:
    global _summary_cache_generation
    _summary_cache_generation += 1
    _request_usage_summary_cache.clear()


def _cached_request_usage_summaries(key: tuple[str, ...] | None) -> dict[str, AccountRequestUsageSummary] | None:
    entry = _request_usage_summary_cache.get(key)
    if entry is None:
        return None
    summaries, expires_at = entry
    if time.monotonic() >= expires_at:
        _request_usage_summary_cache.pop(key, None)
        return None
    return summaries


def _store_request_usage_summaries(
    key: tuple[str, ...] | None, summaries: dict[str, AccountRequestUsageSummary], ttl_seconds: float, generation: int
) -> None:
    if generation != _summary_cache_generation:
        return
    if len(_request_usage_summary_cache) >= _SUMMARY_CACHE_MAX_ENTRIES:
        oldest = min(_request_usage_summary_cache, key=lambda existing: _request_usage_summary_cache[existing][1])
        _request_usage_summary_cache.pop(oldest, None)
    _request_usage_summary_cache[key] = (summaries, time.monotonic() + ttl_seconds)


class AccountIdentityRelockError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExchangeIntentWindow:
    token_hash: str
    replay: bool
    reason: str | None
    available_at: datetime
    ready: bool


def _slot_lock_key(account: Account, *, preserve_unknown_workspace_duplicates: bool = True) -> str:
    return _slot_lock_keys(account, preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates)[0]


def _upsert_identity_candidate_predicates(account: Account, *, include_email: bool) -> list[Any]:
    predicates = [Account.id == account.id]
    if account.chatgpt_account_id:
        predicates.append(Account.chatgpt_account_id == account.chatgpt_account_id)
    if include_email and account.email:
        predicates.append(Account.email == account.email)
    return predicates


from app.modules.accounts.identity_values import _apply_account_updates as _apply_account_updates
from app.modules.accounts.identity_values import _can_reuse_email_fallback as _can_reuse_email_fallback
from app.modules.accounts.identity_values import (
    _is_workspace_less_reauth_for_known_slot as _is_workspace_less_reauth_for_known_slot,
)
from app.modules.accounts.identity_values import _same_unknown_workspace_identity as _same_unknown_workspace_identity
from app.modules.accounts.identity_values import _workspace_slot_identity as _workspace_slot_identity
from app.modules.accounts.identity_values import _workspace_slot_key as _workspace_slot_key
