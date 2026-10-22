from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class SchedulerLeader(Base):
    __tablename__ = "scheduler_leader"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    leader_id: Mapped[str] = mapped_column(String(100), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


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


class ApiFirewallAllowlist(Base):
    __tablename__ = "api_firewall_allowlist"

    ip_address: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class CacheInvalidation(Base):
    __tablename__ = "cache_invalidation"

    namespace: Mapped[str] = mapped_column(String(50), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class RoutingPolicyVersion(Base):
    __tablename__ = "routing_policy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    routing_table_json: Mapped[str] = mapped_column(Text, nullable=False)
    decider_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replay_json: Mapped[str | None] = mapped_column(Text, nullable=True)
