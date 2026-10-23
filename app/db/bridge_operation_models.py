from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values


class FileAccountPin(Base):
    __tablename__ = "file_account_pins"

    file_id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_file_account_pins_expires_at", "expires_at"),)


class CapabilityLineageMarker(Base):
    __tablename__ = "capability_lineage_markers"

    marker_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class HttpBridgeRecoveryAttemptState(str, Enum):
    UNKNOWN = "unknown"
    REPLAYED = "replayed"


class HttpBridgeOperationState(str, Enum):
    SUBMITTED = "submitted"
    UNKNOWN = "unknown"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"
    FAILED = "failed"


class HttpBridgeRecoveryAttemptRecord(Base):
    __tablename__ = "http_bridge_recovery_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("http_bridge_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    replay_safe: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    state: Mapped[HttpBridgeRecoveryAttemptState] = mapped_column(
        SqlEnum(
            HttpBridgeRecoveryAttemptState,
            name="http_bridge_recovery_attempt_state",
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=HttpBridgeRecoveryAttemptState.UNKNOWN,
        server_default=text("'unknown'"),
        nullable=False,
    )
    response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "request_fingerprint",
            name="uq_http_bridge_recovery_attempts_session_fingerprint",
        ),
        Index("idx_http_bridge_recovery_attempts_state", "state", "updated_at"),
    )


class HttpBridgeOperationRecord(Base):
    """Durable identity and outcome for a continuity-bound response.create."""

    __tablename__ = "http_bridge_operations"

    operation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("http_bridge_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'submitted'"))
    response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_dispatch_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    event_bytes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    event_spool_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "request_fingerprint",
            name="uq_http_bridge_operations_session_fingerprint",
        ),
        Index(
            "uq_http_bridge_operations_request_fingerprint",
            "request_fingerprint",
            unique=True,
        ),
        Index("idx_http_bridge_operations_session_parent_state", "session_id", "parent_response_id", "state"),
        Index("idx_http_bridge_operations_parent_state", "parent_response_id", "state", "updated_at"),
        Index("idx_http_bridge_operations_state_updated", "state", "updated_at"),
    )


class HttpBridgeOperationEvent(Base):
    """Replayable upstream SSE blocks for a durable bridge operation."""

    __tablename__ = "http_bridge_operation_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operation_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("http_bridge_operations.operation_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    event_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "event_fingerprint",
            name="uq_http_bridge_operation_events_operation_fingerprint",
        ),
        Index("idx_http_bridge_operation_events_operation_sequence", "operation_id", "sequence_number"),
    )


class HttpBridgeRetryCircuit(Base):
    __tablename__ = "http_bridge_retry_circuits"

    session_key_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    api_key_scope: Mapped[str] = mapped_column(String(255), primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cooldown_until_epoch: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0"),
    )
    last_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at_epoch: Mapped[float] = mapped_column(Float, nullable=False)
