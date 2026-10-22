from __future__ import annotations  # noqa: I001

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, false, func, text, true  # noqa: I001  # fmt: skip
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values


class AccountRoutingPolicy(str, Enum):
    NORMAL = "normal"
    BURN_FIRST = "burn_first"
    PRESERVE = "preserve"


class RequestKind(str, Enum):
    NORMAL = "normal"
    WARMUP = "warmup"


class RequestLog(Base):
    __tablename__ = "request_logs"
    __table_args__ = (Index("idx_logs_useragent_group", "useragent_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String, default="openai", server_default=text("'openai'"), nullable=False)
    api_key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    client_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    caller_user: Mapped[str | None] = mapped_column(String(32), nullable=True)
    caller_user_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    caller_machine: Mapped[str | None] = mapped_column(String(48), nullable=True)
    caller_machine_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stripped_tools: Mapped[str | None] = mapped_column(Text, nullable=True)
    caller_seat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    room: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lane: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispatch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unified_5h_utilization: Mapped[float | None] = mapped_column(Float, nullable=True)
    unified_7d_utilization: Mapped[float | None] = mapped_column(Float, nullable=True)
    unified_headers: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    quota_observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    request_kind: Mapped[str] = mapped_column(
        String, default=RequestKind.NORMAL.value, server_default=text("'normal'"), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    plan_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    useragent: Mapped[str | None] = mapped_column(Text, nullable=True)
    useragent_group: Mapped[str | None] = mapped_column(String, nullable=True)
    transport: Mapped[str | None] = mapped_column(String, nullable=True)
    service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    actual_service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tier: Mapped[str | None] = mapped_column(String(2), nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_phase: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_exception_type: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upstream_error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    bridge_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_proxy_route_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_proxy_pool_id: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_proxy_endpoint_id: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_proxy_fallback_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    upstream_proxy_fail_closed_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    account: Mapped[Account | None] = relationship("Account", back_populates="request_logs")  # type: ignore[unknown-name]  # noqa: F821


class ProxyEndpoint(Base):
    __tablename__ = "proxy_endpoints"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    scheme: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    pool_memberships: Mapped[list["ProxyPoolMember"]] = relationship(
        "ProxyPoolMember",
        back_populates="endpoint",
        cascade="all, delete-orphan",
    )


class ProxyPool(Base):
    __tablename__ = "proxy_pools"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    members: Mapped[list["ProxyPoolMember"]] = relationship(
        "ProxyPoolMember",
        back_populates="pool",
        cascade="all, delete-orphan",
    )
    account_bindings: Mapped[list["AccountProxyBinding"]] = relationship(  # type: ignore[unknown-name]  # noqa: F821
        "AccountProxyBinding",
        back_populates="pool",
    )


class ProxyPoolMember(Base):
    __tablename__ = "proxy_pool_members"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pool_id: Mapped[str] = mapped_column(String, ForeignKey("proxy_pools.id", ondelete="CASCADE"), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("proxy_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    pool: Mapped[ProxyPool] = relationship("ProxyPool", back_populates="members")
    endpoint: Mapped[ProxyEndpoint] = relationship("ProxyEndpoint", back_populates="pool_memberships")

    __table_args__ = (
        UniqueConstraint("pool_id", "endpoint_id", name="uq_proxy_pool_members_pool_endpoint"),
        Index("idx_proxy_pool_members_pool_order", "pool_id", "is_active", "sort_order", "id"),
    )


class AccountProxyBinding(Base):
    __tablename__ = "account_proxy_bindings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    pool_id: Mapped[str] = mapped_column(String, ForeignKey("proxy_pools.id", ondelete="RESTRICT"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship("Account", back_populates="proxy_binding")  # type: ignore[unknown-name]  # noqa: F821
    pool: Mapped[ProxyPool] = relationship("ProxyPool", back_populates="account_bindings")

    __table_args__ = (UniqueConstraint("account_id", name="uq_account_proxy_bindings_account"),)


class AccountLimitWarmup(Base):
    __tablename__ = "account_limit_warmups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    window: Mapped[str] = mapped_column(String, nullable=False)
    reset_at: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(  # type: ignore[unknown-name]  # noqa: F821
        "Account",
        back_populates="limit_warmups",
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "window",
            "reset_at",
            name="uq_account_limit_warmups_account_window_reset",
        ),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (Index("idx_api_keys_member_id", "member_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    member_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("team_members.id", ondelete="SET NULL"),
        nullable=True,
    )
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String, nullable=False)
    allowed_models: Mapped[str | None] = mapped_column(Text, nullable=True)
    apply_to_codex_model: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    enforced_model: Mapped[str | None] = mapped_column(String, nullable=True)
    enforced_reasoning_effort: Mapped[str | None] = mapped_column(String, nullable=True)
    enforced_service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    traffic_class: Mapped[str] = mapped_column(
        String,
        default="foreground",
        server_default=text("'foreground'"),
        nullable=False,
    )
    account_assignment_scope_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    limits: Mapped[list["ApiKeyLimit"]] = relationship(  # type: ignore[unknown-name]  # noqa: F821
        "ApiKeyLimit",
        back_populates="api_key",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    account_assignments: Mapped[list["ApiKeyAccountAssignment"]] = relationship(  # type: ignore[unknown-name]  # noqa: F821
        "ApiKeyAccountAssignment",
        back_populates="api_key",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ApiKeyAccountAssignment(Base):
    __tablename__ = "api_key_accounts"

    api_key_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    api_key: Mapped["ApiKey"] = relationship("ApiKey", back_populates="account_assignments")
    account: Mapped["Account"] = relationship("Account", back_populates="api_key_assignments")  # type: ignore[unknown-name]  # noqa: F821


class LimitType(str, Enum):
    TOTAL_TOKENS = "total_tokens"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    COST_USD = "cost_usd"
    CREDITS = "credits"


class LimitWindow(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    FIVE_HOURS = "5h"
    SEVEN_DAYS = "7d"


class ApiKeyLimit(Base):
    __tablename__ = "api_key_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    limit_type: Mapped[LimitType] = mapped_column(
        SqlEnum(
            LimitType,
            name="limit_type",
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    limit_window: Mapped[LimitWindow] = mapped_column(
        SqlEnum(
            LimitWindow,
            name="limit_window",
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    max_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_value: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    model_filter: Mapped[str | None] = mapped_column(String, nullable=True)
    reset_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    api_key: Mapped["ApiKey"] = relationship("ApiKey", back_populates="limits")
