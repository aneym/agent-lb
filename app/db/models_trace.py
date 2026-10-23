from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RequestContextParts(Base):
    __tablename__ = "request_context_parts"

    request_log_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("request_logs.id", ondelete="CASCADE"), primary_key=True
    )
    body_schema: Mapped[str | None] = mapped_column(String(32))
    body_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    system_chars: Mapped[int | None] = mapped_column(BigInteger)
    tools_chars: Mapped[int | None] = mapped_column(BigInteger)
    history_chars: Mapped[int | None] = mapped_column(BigInteger)
    tool_result_chars: Mapped[int | None] = mapped_column(BigInteger)
    tool_count: Mapped[int | None] = mapped_column(BigInteger)
    message_count: Mapped[int | None] = mapped_column(BigInteger)
    image_count: Mapped[int | None] = mapped_column(BigInteger)
    measured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
