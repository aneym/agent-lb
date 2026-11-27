from __future__ import annotations

from sqlalchemy import delete, update

from app.core.auth import DEFAULT_EMAIL
from app.core.clients.oauth import OAuthError
from app.core.config.settings import get_settings
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME
from app.db.account_state_models import AccountExchangeIntent
from app.db.models import Account
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth.durable import DurableOAuthFlows
from app.modules.oauth.state import ExpectedAccount

ACCOUNT_IDENTITY_CONFLICT_MESSAGE = (
    "Multiple accounts match the authenticated identity. Remove duplicate accounts and retry OAuth."
)


def _email(email: str) -> str:
    return email.strip().casefold()


async def load_expected_account(repo: AccountsRepository, expected_id: str, provider: str) -> ExpectedAccount:
    expected = await repo.get_by_id(expected_id)
    if expected is None or expected.provider != provider or not _is_owner(expected):
        raise OAuthError("account_mismatch", "The target is not a local account for this provider.", 409)
    if not _email(expected.email) or _email(expected.email) == DEFAULT_EMAIL:
        raise OAuthError("account_mismatch", "The target account has no verified sign-in identity.", 409)
    return _snapshot(expected)


def _snapshot(account: Account) -> ExpectedAccount:
    return ExpectedAccount(
        account.id,
        account.email,
        account.provider,
        account.chatgpt_account_id,
        account.workspace_id,
        account.created_at.isoformat(),
    )


def _is_owner(account: Account) -> bool:
    return account.owner_instance in (None, get_settings().local_instance_id)


def _authoritative_upstream_id(expected: ExpectedAccount) -> str | None:
    upstream_id = expected.upstream_id
    if expected.provider == ANTHROPIC_PROVIDER_NAME and upstream_id is not None:
        if _email(upstream_id) == _email(expected.email) or upstream_id.startswith("anthropic_"):
            return None
    return upstream_id


def requires_bound_probe(provider: str) -> bool:
    return provider in {ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME}


async def _replace_expected_account(
    repo: AccountsRepository,
    expected: ExpectedAccount,
    account: Account,
    flow_id: str,
) -> None:
    upstream_id = _authoritative_upstream_id(expected)
    if (
        expected.provider != account.provider
        or _email(expected.email) != _email(account.email)
        or (account.provider == OPENAI_PROVIDER_NAME and not account.chatgpt_account_id)
        or (upstream_id and upstream_id != account.chatgpt_account_id)
        or (expected.workspace_id and expected.workspace_id != account.workspace_id)
    ):
        raise OAuthError(
            "account_mismatch",
            "The signed-in identity does not match the target account; nothing was saved.",
            409,
        )
    target = await repo.reload_by_id(expected.account_id)
    if target is None or _snapshot(target) != expected or not _is_owner(target):
        raise OAuthError("account_mismatch", "The target account changed; nothing was saved.", 409)

    values: dict[str, object] = {
        "chatgpt_account_id": account.chatgpt_account_id or expected.upstream_id,
        "workspace_id": account.workspace_id or expected.workspace_id,
        "workspace_label": account.workspace_label or target.workspace_label,
        "seat_type": account.seat_type or target.seat_type,
        "plan_type": account.plan_type,
        "access_token_encrypted": account.access_token_encrypted,
        "refresh_token_encrypted": account.refresh_token_encrypted,
        "id_token_encrypted": account.id_token_encrypted,
        "last_refresh": account.last_refresh,
        "access_expires_at": account.access_expires_at,
        "status": account.status,
        "deactivation_reason": None,
        "reset_at": None,
        "blocked_at": None,
    }
    if account.warmup_until is not None and target.warmup_until is None:
        values.update(
            warmup_until=account.warmup_until,
            warmup_capacity_percent=account.warmup_capacity_percent,
            warmup_max_concurrent=account.warmup_max_concurrent,
            warmup_owner_instance=account.warmup_owner_instance,
        )

    account_id = await repo.session.scalar(
        update(Account)
        .where(
            Account.id == expected.account_id,
            Account.provider == expected.provider,
            Account.email == expected.email,
            Account.chatgpt_account_id == expected.upstream_id,
            Account.workspace_id == expected.workspace_id,
            Account.owner_instance == target.owner_instance,
            Account.refresh_token_encrypted == target.refresh_token_encrypted,
        )
        .values(**values)
        .returning(Account.id)
    )
    if account_id is None:
        raise OAuthError("account_mismatch", "The target account changed; nothing was saved.", 409)
    await repo.session.execute(delete(AccountExchangeIntent).where(AccountExchangeIntent.account_id == account_id))
    if not await DurableOAuthFlows.mark_bound_save(
        repo.session, flow_id, needs_probe=requires_bound_probe(account.provider)
    ):
        raise OAuthError("expired_flow", "This sign-in has expired; nothing was saved.", 409)
    await repo.session.commit()


async def save_signed_in_account(
    repo: AccountsRepository,
    account: Account,
    expected: ExpectedAccount | None,
    *,
    flow_id: str,
) -> None:
    if expected is not None:
        await _replace_expected_account(repo, expected, account, flow_id)
        return
    if account.chatgpt_account_id and account.workspace_id is None:
        await repo.upsert(account, merge_by_email=False, merge_by_chatgpt_identity=True)
    else:
        await repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)
