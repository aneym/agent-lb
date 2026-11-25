from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.providers.gemini import GEMINI_PROVIDER_NAME
from app.db.models import Account
from app.db.request_models import ApiKey, ApiKeyAccountAssignment
from app.db.session import get_background_session

logger = logging.getLogger(__name__)


async def attach_gemini_pool_to_scoped_keys() -> int:
    async with get_background_session() as session:
        gemini_ids = set(
            (await session.execute(select(Account.id).where(Account.provider == GEMINI_PROVIDER_NAME))).scalars()
        )
        if not gemini_ids:
            return 0
        rows = await session.execute(
            select(ApiKeyAccountAssignment.api_key_id, ApiKeyAccountAssignment.account_id)
            .join(ApiKey, ApiKey.id == ApiKeyAccountAssignment.api_key_id)
            .where(ApiKey.account_assignment_scope_enabled.is_(True))
        )
        assigned: dict[str, set[str]] = {}
        for key_id, account_id in rows.all():
            assigned.setdefault(key_id, set()).add(account_id)
        added = 0
        for key_id, account_ids in assigned.items():
            if not account_ids & gemini_ids:
                continue
            for account_id in sorted(gemini_ids - account_ids):
                session.add(ApiKeyAccountAssignment(api_key_id=key_id, account_id=account_id))
                added += 1
        if added:
            await session.commit()
            logger.info("gemini_pool_scope_attached assignments=%s", added)
        return added
