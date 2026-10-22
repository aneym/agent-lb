from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, false, func, text, true  # noqa: I001  # fmt: skip
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values


class AccountStatus(str, Enum):
    ACTIVE = "active"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    PAUSED = "paused"
    REAUTH_REQUIRED = "reauth_required"
    EXCHANGE_UNCERTAIN = "exchange_uncertain"
    DEACTIVATED = "deactivated"


class StickySessionKind(str, Enum):
    CODEX_SESSION = "codex_session"
    STICKY_THREAD = "sticky_thread"
    PROMPT_CACHE = "prompt_cache"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, default="openai", server_default=text("'openai'"), nullable=False)
    chatgpt_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    alias: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_label: Mapped[str | None] = mapped_column(String, nullable=True)
    seat_type: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_type: Mapped[str] = mapped_column(String, nullable=False)
    routing_policy: Mapped[str] = mapped_column(
        String, default="normal", server_default=text("'normal'"), nullable=False
    )

    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    id_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    last_refresh: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    status: Mapped[AccountStatus] = mapped_column(
        SqlEnum(
            AccountStatus,
            name="account_status",
            validate_strings=True,
            values_callable=enum_values,
        ),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )
    deactivation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(String, nullable=True)
    subscription_next_charge_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    subscription_current_period_end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    subscription_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    subscription_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    subscription_last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    subscription_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocked_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_warmup_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    security_work_authorized: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    owner_instance: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    api_key_assignments: Mapped[list["ApiKeyAccountAssignment"]] = relationship(  # type: ignore[unknown-name]  # noqa: F821
        "ApiKeyAccountAssignment",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    request_logs: Mapped[list["RequestLog"]] = relationship("RequestLog", back_populates="account")  # type: ignore[unknown-name]  # noqa: F821
    limit_warmups: Mapped[list["AccountLimitWarmup"]] = relationship(  # type: ignore[unknown-name]  # noqa: F821
        "AccountLimitWarmup",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    proxy_binding: Mapped["AccountProxyBinding | None"] = relationship(  # type: ignore[unknown-name]  # noqa: F821
        "AccountProxyBinding",
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
    )


class UsageHistory(Base):
    __tablename__ = "usage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String, default="openai", server_default=text("'openai'"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    window: Mapped[str | None] = mapped_column(String, nullable=True)
    used_percent: Mapped[float] = mapped_column(Float, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_has: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    credits_unlimited: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    credits_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    credits_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    credits_spent: Mapped[float | None] = mapped_column(Float, nullable=True)


class AdditionalUsageHistory(Base):
    __tablename__ = "additional_usage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    quota_key: Mapped[str] = mapped_column(String, nullable=False)
    limit_name: Mapped[str] = mapped_column(String, nullable=False)
    metered_feature: Mapped[str] = mapped_column(String, nullable=False)
    window: Mapped[str] = mapped_column(String, nullable=False)
    used_percent: Mapped[float] = mapped_column(Float, nullable=False)
    reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class DashboardSettings(Base):
    __tablename__ = "dashboard_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sticky_threads_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    upstream_stream_transport: Mapped[str] = mapped_column(
        String,
        default="default",
        server_default=text("'default'"),
        nullable=False,
    )
    prefer_earlier_reset_accounts: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    prefer_earlier_reset_window: Mapped[str] = mapped_column(
        String,
        default="secondary",
        server_default=text("'secondary'"),
        nullable=False,
    )
    routing_strategy: Mapped[str] = mapped_column(
        String,
        default="capacity_weighted",
        server_default=text("'capacity_weighted'"),
        nullable=False,
    )
    relative_availability_power: Mapped[float] = mapped_column(
        Float,
        default=2.0,
        server_default=text("2.0"),
        nullable=False,
    )
    relative_availability_top_k: Mapped[int] = mapped_column(
        Integer,
        default=5,
        server_default=text("5"),
        nullable=False,
    )
    single_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    openai_cache_affinity_max_age_seconds: Mapped[int] = mapped_column(
        Integer,
        default=1800,
        server_default=text("1800"),
        nullable=False,
    )
    dashboard_session_ttl_seconds: Mapped[int] = mapped_column(
        Integer,
        default=43200,
        server_default=text("43200"),
        nullable=False,
    )
    import_without_overwrite: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    totp_required_on_login: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    bootstrap_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    bootstrap_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    api_key_auth_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    team_mode_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    team_public_base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_last_verified_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: Mapped[int] = mapped_column(
        Integer,
        default=3600,
        server_default=text("3600"),
        nullable=False,
    )
    http_responses_session_bridge_gateway_safe_mode: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    upstream_proxy_routing_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    upstream_proxy_default_pool_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("proxy_pools.id", ondelete="SET NULL"),
        nullable=True,
    )
    sticky_reallocation_budget_threshold_pct: Mapped[float] = mapped_column(
        Float,
        default=95.0,
        server_default=text("95.0"),
        nullable=False,
    )
    sticky_reallocation_primary_budget_threshold_pct: Mapped[float] = mapped_column(
        Float,
        default=95.0,
        server_default=text("95.0"),
        nullable=False,
    )
    sticky_reallocation_secondary_budget_threshold_pct: Mapped[float] = mapped_column(
        Float,
        default=100.0,
        server_default=text("100.0"),
        nullable=False,
    )
    additional_quota_routing_policies_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        server_default=text("'{}'"),
        nullable=False,
    )
    limit_warmup_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    limit_warmup_windows: Mapped[str] = mapped_column(
        String,
        default="both",
        server_default=text("'both'"),
        nullable=False,
    )
    limit_warmup_model: Mapped[str] = mapped_column(
        String,
        default="auto",
        server_default=text("'auto'"),
        nullable=False,
    )
    limit_warmup_prompt: Mapped[str] = mapped_column(
        Text,
        default="Say OK.",
        server_default=text("'Say OK.'"),
        nullable=False,
    )
    limit_warmup_cooldown_seconds: Mapped[int] = mapped_column(
        Integer,
        default=3600,
        server_default=text("3600"),
        nullable=False,
    )
    limit_warmup_min_available_percent: Mapped[float] = mapped_column(
        Float,
        default=100.0,
        server_default=text("100.0"),
    )
    weekly_pace_working_days: Mapped[str] = mapped_column(
        String,
        default="0,1,2,3,4,5,6",
        server_default=text("'0,1,2,3,4,5,6'"),
        nullable=False,
    )
    warmup_model: Mapped[str] = mapped_column(
        String,
        default="gpt-5.4-mini",
        server_default=text("'gpt-5.4-mini'"),
        nullable=False,
    )
    additional_quota_routing_policies_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        server_default=text("'{}'"),
        nullable=False,
    )
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
