import logging
from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context_parts.capture import Captured
from app.core.utils.time import utcnow
from app.db.models_trace import RequestContextParts

logger = logging.getLogger(__name__)


async def store_context_parts(session: AsyncSession, log_id: int, captured: Captured | None) -> None:
    if captured is None:
        return
    try:
        values = asdict(captured.parts) if captured.parts is not None else {}
        session.add(
            RequestContextParts(request_log_id=log_id, body_bytes=captured.body_bytes, measured_at=utcnow(), **values)
        )
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        logger.warning("Context parts storage failed for log %s", log_id)
