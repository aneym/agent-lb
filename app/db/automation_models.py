from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models import Account


class AutomationJob(Base):
    __tablename__ = "automation_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    schedule_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="daily",
        server_default=text("'daily'"),
    )
    schedule_time: Mapped[str] = mapped_column(String(5), nullable=False)
    schedule_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    schedule_days: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="mon,tue,wed,thu,fri,sat,sun",
        server_default=text("'mon,tue,wed,thu,fri,sat,sun'"),
    )
    schedule_threshold_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    include_paused_accounts: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    account_scope_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    model: Mapped[str] = mapped_column(String, nullable=False)
    reasoning_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="ping", server_default=text("'ping'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account_links: Mapped[list["AutomationJobAccount"]] = relationship(
        "AutomationJobAccount",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    runs: Mapped[list["AutomationRun"]] = relationship(
        "AutomationRun",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    run_cycles: Mapped[list["AutomationRunCycle"]] = relationship(
        "AutomationRunCycle",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class AutomationJobAccount(Base):
    __tablename__ = "automation_job_accounts"
    __table_args__ = (UniqueConstraint("job_id", "position", name="uq_automation_job_accounts_position"),)

    job_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("automation_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    job: Mapped[AutomationJob] = relationship("AutomationJob", back_populates="account_links")
    account: Mapped[Account] = relationship("Account")


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("automation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    cycle_key: Mapped[str] = mapped_column(String(160), nullable=False)
    cycle_expected_accounts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cycle_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", server_default=text("'running'"))
    account_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    job: Mapped[AutomationJob] = relationship("AutomationJob", back_populates="runs")
    account: Mapped[Account | None] = relationship("Account")


class AutomationRunCycle(Base):
    __tablename__ = "automation_run_cycles"

    cycle_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("automation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_expected_accounts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    cycle_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    include_paused_accounts: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    job: Mapped[AutomationJob] = relationship("AutomationJob", back_populates="run_cycles")
    cycle_accounts: Mapped[list["AutomationRunCycleAccount"]] = relationship(
        "AutomationRunCycleAccount",
        back_populates="cycle",
        cascade="all, delete-orphan",
    )


class AutomationRunCycleAccount(Base):
    __tablename__ = "automation_run_cycle_accounts"
    __table_args__ = (UniqueConstraint("cycle_key", "position", name="uq_automation_run_cycle_accounts_position"),)

    cycle_key: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("automation_run_cycles.cycle_key", ondelete="CASCADE"),
        primary_key=True,
    )
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    slot_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    cycle: Mapped[AutomationRunCycle] = relationship("AutomationRunCycle", back_populates="cycle_accounts")
