import logging
from dataclasses import asdict

from sqlalchemy import insert as sa_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context_parts.capture import Captured
from app.core.utils.time import utcnow
from app.db.models_trace import RequestContextParts

logger = logging.getLogger(__name__)
_INSERT_CONTEXT_PARTS_STMT = sa_insert(RequestContextParts.__table__)


async def insert_context_parts(session: AsyncSession, log_id: int, captured: Captured | None) -> None:
    if captured is None:
        return
    p = captured.parts
    values: dict[str, object] = {
        "request_log_id": log_id,
        "body_bytes": captured.body_bytes,
        "measured_at": utcnow(),
        "body_schema": p.body_schema if p is not None else None,
        "system_chars": p.system_chars if p is not None else None,
        "tools_chars": p.tools_chars if p is not None else None,
        "history_chars": p.history_chars if p is not None else None,
        "tool_result_chars": p.tool_result_chars if p is not None else None,
        "tool_count": p.tool_count if p is not None else None,
        "message_count": p.message_count if p is not None else None,
        "image_count": p.image_count if p is not None else None,
    }
    try:
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            async with session.begin_nested():
                await session.execute(_INSERT_CONTEXT_PARTS_STMT, values)
        else:
            await session.execute(_INSERT_CONTEXT_PARTS_STMT, values)
    except Exception:
        logger.warning("Context parts storage failed for log %s", log_id)


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
