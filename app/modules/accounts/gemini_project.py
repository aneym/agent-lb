from __future__ import annotations

from sqlalchemy import or_, update

from app.core.balancer.failures import permanent_failure_reason
from app.core.clients.code_assist import CodeAssistError, resolve_code_assist_project
from app.core.clients.google_oauth import google_access_token_rejected
from app.db.models import Account
from app.db.session import get_background_session
from app.modules.proxy.account_cache import get_account_selection_cache

UNCONFIRMED_REJECTION_STATUS = 503
GEMINI_TERMS_DISABLED_CODE = "gemini_terms_disabled"
GEMINI_TERMS_DISABLED_REASON = permanent_failure_reason(GEMINI_TERMS_DISABLED_CODE)


async def probe_gemini_account(account: Account, access_token: str) -> int:
    try:
        project_id = await resolve_code_assist_project(access_token)
    except CodeAssistError as exc:
        if exc.status_code == 401 and not await google_access_token_rejected(access_token):
            return UNCONFIRMED_REJECTION_STATUS
        return exc.status_code
    if account.workspace_id != project_id:
        await _store_project(account, project_id)
    return 200


async def _store_project(account: Account, project_id: str) -> None:
    async with get_background_session() as session:
        prior = (
            Account.workspace_id.is_(None)
            if account.workspace_id is None
            else Account.workspace_id == account.workspace_id
        )
        await session.execute(
            update(Account)
            .where(Account.id == account.id, Account.access_token_encrypted == account.access_token_encrypted, prior)
            .values(workspace_id=project_id)
        )
        await session.commit()
    get_account_selection_cache().invalidate()


async def remember_gemini_project(account_id: str, project_id: str) -> None:
    async with get_background_session() as session:
        await session.execute(
            update(Account)
            .where(Account.id == account_id, or_(Account.workspace_id.is_(None), Account.workspace_id == ""))
            .values(workspace_id=project_id)
        )
        await session.commit()
    get_account_selection_cache().invalidate()
