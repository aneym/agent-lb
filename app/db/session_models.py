from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values


class StickySessionKind(str, Enum):
    CODEX_SESSION = "codex_session"
    STICKY_THREAD = "sticky_thread"
    PROMPT_CACHE = "prompt_cache"


class StickySession(Base):
    __tablename__ = "sticky_sessions"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[StickySessionKind] = mapped_column(
        SqlEnum(
            StickySessionKind,
            name="sticky_session_kind",
            validate_strings=True,
            values_callable=enum_values,
        ),
        primary_key=True,
        default=StickySessionKind.STICKY_THREAD,
        server_default=text("'sticky_thread'"),
        nullable=False,
    )
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class HttpBridgeSessionState(str, Enum):
    ACTIVE = "active"
    DRAINING = "draining"
    CLOSED = "closed"


class HttpBridgeSessionRecord(Base):
    __tablename__ = "http_bridge_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_key_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    session_key_value: Mapped[str] = mapped_column(Text, nullable=False)
    session_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_instance_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[HttpBridgeSessionState] = mapped_column(
        SqlEnum(
            HttpBridgeSessionState,
            name="http_bridge_session_state",
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=HttpBridgeSessionState.ACTIVE,
        server_default=text("'active'"),
        nullable=False,
    )
    account_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_turn_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_input_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_input_full_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    aliases: Mapped[list["HttpBridgeSessionAlias"]] = relationship(
        "HttpBridgeSessionAlias",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "session_key_kind",
            "session_key_hash",
            "api_key_scope",
            name="uq_http_bridge_sessions_session_key",
        ),
    )


class HttpBridgeSessionAlias(Base):
    __tablename__ = "http_bridge_session_aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("http_bridge_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    alias_value: Mapped[str] = mapped_column(Text, nullable=False)
    alias_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    session: Mapped[HttpBridgeSessionRecord] = relationship(
        "HttpBridgeSessionRecord",
        back_populates="aliases",
    )

    __table_args__ = (
        UniqueConstraint(
            "alias_kind",
            "alias_hash",
            "api_key_scope",
            name="uq_http_bridge_session_aliases_alias",
        ),
    )
