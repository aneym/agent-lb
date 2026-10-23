from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_values


class AccountTransferDirection(str, Enum):
    CHECKOUT = "checkout"
    CHECKIN = "checkin"


class AccountTransferState(str, Enum):
    PENDING = "pending"
    ABORTING = "aborting"
    SETTLED = "settled"
    ABORTED = "aborted"


class FederationUsageDaily(Base):
    __tablename__ = "federation_usage_daily"

    instance_id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    requests: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    session_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AccountTransfer(Base):
    __tablename__ = "account_transfers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    nonce: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    direction: Mapped[AccountTransferDirection] = mapped_column(
        SqlEnum(
            AccountTransferDirection,
            name="account_transfer_direction",
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
    )

    counterparty_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[AccountTransferState] = mapped_column(
        SqlEnum(
            AccountTransferState,
            name="account_transfer_state",
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=AccountTransferState.PENDING,
        server_default=text("'pending'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "idx_account_transfers_account_direction_state",
            "account_id",
            "direction",
            "state",
        ),
    )


class BridgeRingMember(Base):
    __tablename__ = "bridge_ring_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    instance_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
