from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
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
    from app.db.models import ApiKey, RequestLog


class ModelSource(Base):
    __tablename__ = "model_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(
        String,
        default="openai_compatible",
        server_default=text("'openai_compatible'"),
        nullable=False,
    )
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    health_status: Mapped[str] = mapped_column(
        String,
        default="unknown",
        server_default=text("'unknown'"),
        nullable=False,
    )
    supports_chat_completions: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    supports_responses: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    supports_audio_transcriptions: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    supports_embeddings: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    models: Mapped[list["ModelSourceModel"]] = relationship(
        "ModelSourceModel",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    api_key_assignments: Mapped[list["ApiKeyModelSourceAssignment"]] = relationship(
        "ApiKeyModelSourceAssignment",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    request_logs: Mapped[list["RequestLog"]] = relationship(
        "RequestLog",
        back_populates="model_source",
        primaryjoin="ModelSource.id == foreign(RequestLog.model_source_id)",
        viewonly=True,
    )


class ModelSourceModel(Base):
    __tablename__ = "model_source_models"
    __table_args__ = (UniqueConstraint("source_id", "model", name="uq_model_source_models_source_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("model_sources.id", ondelete="CASCADE"), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    input_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    cached_input_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_per_minute: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    source: Mapped["ModelSource"] = relationship("ModelSource", back_populates="models")


class ApiKeyModelSourceAssignment(Base):
    __tablename__ = "api_key_model_sources"

    api_key_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("model_sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    api_key: Mapped["ApiKey"] = relationship("ApiKey", back_populates="source_assignments")
    source: Mapped["ModelSource"] = relationship("ModelSource", back_populates="api_key_assignments")
