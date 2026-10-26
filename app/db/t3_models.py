from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class T3Thread(Base):
    __tablename__ = "t3_threads"
    __table_args__ = (Index("ix_t3_threads_root", "host", "root_thread_id"),)

    host: Mapped[str] = mapped_column(String(32), primary_key=True)
    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_root: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_instance: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    relationship: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    creation_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class T3NativeSession(Base):
    __tablename__ = "t3_native_sessions"
    __table_args__ = (Index("ix_t3_native_sessions_thread", "host", "thread_id"),)

    native_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    host: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    driver: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_instance: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
