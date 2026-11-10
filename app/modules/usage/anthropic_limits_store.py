from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.db.session import get_background_session, sqlite_writer_section
from app.db.usage_limit_models import AnthropicUsageLimits

logger = logging.getLogger(__name__)


async def record_anthropic_limits(account_id: str, limits: list[dict[str, Any]], recorded_at: datetime) -> None:
    try:
        async with get_background_session() as session:
            session.add(AnthropicUsageLimits(account_id=account_id, limits=limits, recorded_at=recorded_at))
            async with sqlite_writer_section(session):
                await session.commit()
    except Exception:
        logger.warning("Failed to store Anthropic usage limits account_id=%s", account_id, exc_info=True)
