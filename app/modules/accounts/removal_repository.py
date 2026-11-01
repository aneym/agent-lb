from __future__ import annotations

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import Account, UsageHistory
from app.db.request_models import RequestLog
from app.db.session_models import StickySession
from app.modules.request_logs import daily_rollup


class AccountRemovalRepository:
    _session: AsyncSession

    async def delete(self, account_id: str, *, delete_history: bool = False) -> bool:
        await self._session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
        await daily_rollup.forget_account(self._session, account_id, delete_history=delete_history)
        if delete_history:
            await self._session.execute(delete(RequestLog).where(RequestLog.account_id == account_id))
        else:
            await self._session.execute(
                update(RequestLog)
                .where(RequestLog.account_id == account_id)
                .values(account_id=None, deleted_at=utcnow()),
            )
        await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
        result = await self._session.execute(delete(Account).where(Account.id == account_id).returning(Account.id))
        await self._session.commit()
        return result.scalar_one_or_none() is not None
