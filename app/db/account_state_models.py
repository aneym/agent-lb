from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AccountExchangeIntent(Base):
    __tablename__ = "account_exchange_intents"

    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    refresh_token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    replay: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auto_resets: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0", nullable=False)


class AccountResumeSchedule(Base):
    __tablename__ = "account_resume_schedules"

    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    resume_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ResetCreditAttempt(Base):
    __tablename__ = "reset_credit_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    credit_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    result_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    windows_reset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str] = mapped_column(String(36), nullable=False)
