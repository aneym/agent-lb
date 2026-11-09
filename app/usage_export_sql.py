from __future__ import annotations

from typing import Any


def rows(cursor: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cursor.execute(sql, params or {})
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
