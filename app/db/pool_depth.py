from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from app.db.session import background_engine, engine


def _engine_depth(engine: AsyncEngine) -> dict[str, Any]:
    pool = engine.sync_engine.pool
    if not isinstance(pool, QueuePool):
        return {"kind": type(pool).__name__}
    return {
        "kind": "queue",
        "size": pool.size(),
        "max_overflow": pool._max_overflow,
        "checked_out": pool.checkedout(),
        "idle": pool.checkedin(),
        "overflow": max(pool.overflow(), 0),
    }


def pool_depth() -> dict[str, dict[str, Any]]:
    depth = {"main": _engine_depth(engine)}
    background = background_engine()
    if background is not None and background is not engine:
        depth["background"] = _engine_depth(background)
    return depth
