from __future__ import annotations

from app.core.clients.oauth import OAuthError
from app.db.models import Account
from app.modules.accounts.repository import AccountsRepository


def _same(left: str | None, right: str | None) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()


async def _require_expected_account(repo: AccountsRepository, expected_id: str, signed_in: Account) -> None:
    expected = await repo.get_by_id(expected_id)
    if expected is None:
        raise OAuthError("account_mismatch", "The account this sign-in was started for no longer exists.", 409)
    same_identity = (
        expected.provider == signed_in.provider
        and _same(expected.email, signed_in.email)
        and (
            not expected.chatgpt_account_id
            or not signed_in.chatgpt_account_id
            or expected.chatgpt_account_id == signed_in.chatgpt_account_id
        )
        and (not expected.workspace_id or not signed_in.workspace_id or expected.workspace_id == signed_in.workspace_id)
    )
    if not same_identity:
        raise OAuthError(
            "account_mismatch",
            f"Signed in as {signed_in.email}, but this sign-in was started for {expected.email}; nothing was saved.",
            409,
        )


async def save_signed_in_account(repo: AccountsRepository, account: Account, expected_id: str | None) -> None:
    if expected_id:
        await _require_expected_account(repo, expected_id, account)
        account.id = expected_id
        await repo.upsert(account, merge_by_email=True)
        return
    if account.chatgpt_account_id and account.workspace_id is None:
        await repo.upsert(account, merge_by_email=False, merge_by_chatgpt_identity=True)
    else:
        await repo.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)
