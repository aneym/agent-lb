from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QuotaPlannerSettings(Base):
    __tablename__ = "quota_planner_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    mode: Mapped[str] = mapped_column(String, default="shadow", server_default=text("'shadow'"), nullable=False)
    timezone: Mapped[str] = mapped_column(String, default="UTC", server_default=text("'UTC'"), nullable=False)
    working_days_json: Mapped[str] = mapped_column(
        Text,
        default="[0,1,2,3,4]",
        server_default=text("'[0,1,2,3,4]'"),
        nullable=False,
    )
    working_hours_start: Mapped[str] = mapped_column(
        String,
        default="09:00",
        server_default=text("'09:00'"),
        nullable=False,
    )
    working_hours_end: Mapped[str] = mapped_column(
        String,
        default="18:00",
        server_default=text("'18:00'"),
        nullable=False,
    )
    prewarm_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    prewarm_lead_minutes: Mapped[int] = mapped_column(Integer, default=300, server_default=text("300"), nullable=False)

    seed_target_offset_minutes: Mapped[int] = mapped_column(
        Integer,
        default=120,
        server_default=text("120"),
        nullable=False,
    )
    max_warmups_per_day: Mapped[int] = mapped_column(Integer, default=3, server_default=text("3"), nullable=False)
    max_warmup_credits_per_day: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        server_default=text("0.0"),
        nullable=False,
    )
    min_expected_gain: Mapped[float] = mapped_column(Float, default=1.0, server_default=text("1.0"), nullable=False)
    forecast_quantile: Mapped[str] = mapped_column(String, default="p75", server_default=text("'p75'"), nullable=False)
    allow_synthetic_traffic: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    warmup_model_preference: Mapped[str | None] = mapped_column(String, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class QuotaPlannerDecision(Base):
    __tablename__ = "quota_planner_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0.0"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    forecast_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="planned", server_default=text("'planned'"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)


class QuotaWindowObservation(Base):
    __tablename__ = "quota_window_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_remaining_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    primary_reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    secondary_remaining_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    secondary_reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(String, default="unknown", server_default=text("'unknown'"), nullable=False)
