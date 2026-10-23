from __future__ import annotations

import hashlib
import uuid
from typing import Any

from app.core.providers import normalize_provider_name
from app.db.models import Account


def _workspace_slot_key(account: Account) -> str | None:
    if account.workspace_id:
        return account.workspace_id
    if account.workspace_label:
        return account.workspace_label
    return None


def _workspace_slot_identity(account: Account) -> tuple[Any, str] | None:
    if account.workspace_id:
        return (Account.workspace_id, account.workspace_id)
    if account.workspace_label:
        return (Account.workspace_label, account.workspace_label)
    return None


_SETTINGS_ROW_ID = 1
_DUPLICATE_ACCOUNT_SUFFIX = "__copy"


def _apply_account_updates(target: Account, source: Account) -> None:
    if normalize_provider_name(target.provider) != normalize_provider_name(source.provider):
        raise ValueError("Cannot replace credentials across providers")
    target.provider = normalize_provider_name(source.provider)
    if source.chatgpt_account_id is not None:
        target.chatgpt_account_id = source.chatgpt_account_id
    if source.chatgpt_user_id is not None:
        target.chatgpt_user_id = source.chatgpt_user_id
    target.email = source.email
    if source.workspace_id is not None or target.workspace_id is None:
        target.workspace_id = source.workspace_id
        target.workspace_label = source.workspace_label
        target.seat_type = source.seat_type
    if not target.codex_installation_id:
        target.codex_installation_id = source.codex_installation_id or str(uuid.uuid4())
    target.plan_type = source.plan_type
    target.access_token_encrypted = source.access_token_encrypted
    target.refresh_token_encrypted = source.refresh_token_encrypted
    target.id_token_encrypted = source.id_token_encrypted
    target.last_refresh = source.last_refresh
    target.access_expires_at = source.access_expires_at
    target.status = source.status
    target.deactivation_reason = source.deactivation_reason
    target.reset_at = source.reset_at
    target.blocked_at = source.blocked_at
    target.delete_requested_at = None
    target.delete_history_requested = False


def _slot_lock_keys(account: Account, *, preserve_unknown_workspace_duplicates: bool = True) -> tuple[str, ...]:
    keys: list[str] = []
    workspace_key = _workspace_slot_key(account)
    if account.chatgpt_account_id:
        if workspace_key:
            keys.append(f"slot:{account.chatgpt_account_id}:{workspace_key}")
        elif account.email:
            keys.append(f"slot:{account.chatgpt_account_id}:{account.email}")
    if account.email and workspace_key:
        keys.append(f"slot-email:{account.email}:{workspace_key}")
        if not preserve_unknown_workspace_duplicates:
            keys.append(f"slot-email-unknown:{account.email}")
    if keys:
        return tuple(keys)
    if account.email and (not preserve_unknown_workspace_duplicates):
        return (f"slot-email-unknown:{account.email}",)
    return (f"slot-local:{account.id}",)


def _same_unknown_workspace_identity(existing: Account, incoming: Account) -> bool:
    return (
        normalize_provider_name(existing.provider) == normalize_provider_name(incoming.provider)
        and _workspace_slot_key(existing) is None
        and _workspace_slot_key(incoming) is None
        and (existing.chatgpt_account_id == incoming.chatgpt_account_id)
        and (existing.email == incoming.email)
    )


def _is_workspace_less_reauth_for_known_slot(
    existing: Account, incoming: Account, *, merge_by_chatgpt_identity: bool
) -> bool:
    return (
        normalize_provider_name(existing.provider) == normalize_provider_name(incoming.provider)
        and merge_by_chatgpt_identity
        and existing.workspace_id is not None
        and (incoming.workspace_id is None)
        and (incoming.chatgpt_account_id is not None)
        and (existing.chatgpt_account_id == incoming.chatgpt_account_id)
        and (existing.email == incoming.email)
    )


def _can_reuse_email_fallback(existing: Account, incoming: Account) -> bool:
    if normalize_provider_name(existing.provider) != normalize_provider_name(incoming.provider):
        return False
    existing_workspace_key = _workspace_slot_key(existing)
    incoming_workspace_key = _workspace_slot_key(incoming)
    if existing_workspace_key and incoming_workspace_key and (existing_workspace_key != incoming_workspace_key):
        return False
    return (
        not incoming.chatgpt_account_id
        or not existing.chatgpt_account_id
        or existing.chatgpt_account_id == incoming.chatgpt_account_id
    )


def _advisory_lock_key(scope: str, value: str) -> int:
    digest = hashlib.sha256(f"{scope}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
