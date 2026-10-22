from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values
from app.db.models import ApiKeyLimit


class TeamMemberStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[TeamMemberStatus] = mapped_column(
        SqlEnum(
            TeamMemberStatus,
            name="team_member_status",
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=TeamMemberStatus.ACTIVE,
        server_default=text("'active'"),
        nullable=False,
    )
    cost_cap_day_usd: Mapped[float | None] = mapped_column(Numeric(12, 4, asdecimal=False), nullable=True)
    cost_cap_week_usd: Mapped[float | None] = mapped_column(Numeric(12, 4, asdecimal=False), nullable=True)
    cost_cap_month_usd: Mapped[float | None] = mapped_column(Numeric(12, 4, asdecimal=False), nullable=True)
    token_cap_day: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    token_cap_week: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    token_cap_month: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pool_share_percent: Mapped[float | None] = mapped_column(Numeric(6, 3, asdecimal=False), nullable=True)
    allowed_models: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ApiKeyUsageReservation(Base):
    __tablename__ = "api_key_usage_reservations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    api_key_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="reserved")
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_microdollars: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    items: Mapped[list["ApiKeyUsageReservationItem"]] = relationship(
        "ApiKeyUsageReservationItem",
        back_populates="reservation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ApiKeyUsageReservationItem(Base):
    __tablename__ = "api_key_usage_reservation_items"
    __table_args__ = (UniqueConstraint("reservation_id", "limit_id", name="uq_reservation_limit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reservation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_key_usage_reservations.id", ondelete="CASCADE"),
        nullable=False,
    )
    limit_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("api_key_limits.id", ondelete="CASCADE"),
        nullable=False,
    )
    limit_type: Mapped[str] = mapped_column(String, nullable=False)
    reserved_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_delta: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_reset_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    reservation: Mapped[ApiKeyUsageReservation] = relationship(
        "ApiKeyUsageReservation",
        back_populates="items",
    )
    limit: Mapped[ApiKeyLimit] = relationship("ApiKeyLimit")


class RateLimitAttempt(Base):
    __tablename__ = "rate_limit_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
