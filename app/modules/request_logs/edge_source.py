from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import RequestLog


def exclude_forwarded_edge_logs() -> ColumnElement[bool]:
    """Rows this instance logged itself. Forwarded edge copies use source edge:<id>."""
    return or_(RequestLog.source.is_(None), RequestLog.source.not_like("edge:%"))
