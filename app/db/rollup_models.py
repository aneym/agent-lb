from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RequestLogDaily(Base):
    __tablename__ = "request_log_daily"
    __table_args__ = (
        Index("ix_request_log_daily_day", "day"),
        Index("ix_request_log_daily_archive", "archive_id"),
        Index("ix_request_log_daily_account", "account_id"),
        Index("ix_request_log_daily_api_key", "api_key_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archive_id: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    api_key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    request_kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requests: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_or_reasoning_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_creation_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    latency_ms_sum: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latency_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
