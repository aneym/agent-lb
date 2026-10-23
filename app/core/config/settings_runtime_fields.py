from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class SettingsRuntimeFields(BaseSettings):
    database_alembic_auto_remap_enabled: bool = True
    database_migration_lock_timeout_seconds: float = Field(default=300.0, gt=0)
    http_downstream_transport_policy: Literal["smart", "always_http", "always_websocket", "pinned"] = "smart"
    token_refresh_claim_ttl_seconds: float = Field(default=30.0, gt=0)
    live_usage_ingestion_enabled: bool = True
    rate_limit_reset_credits_refresh_enabled: bool = True
    rate_limit_reset_credits_refresh_interval_seconds: int = Field(default=60, gt=0)
    http_responses_session_bridge_stuck_gate_retire_after_seconds: float = Field(default=300.0, gt=0)
    http_responses_session_bridge_anchor_poison_failure_threshold: int = Field(default=7, ge=1, le=100)
    http_responses_session_bridge_clean_close_retry_jitter_max_seconds: float = Field(default=2.0, ge=0, le=30.0)
    http_responses_session_bridge_operation_ledger_enabled: bool = True
    http_responses_session_bridge_operation_event_spool_max_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    http_responses_session_bridge_operation_event_spool_batch_size: int = Field(default=32, gt=0, le=256)
    http_responses_session_bridge_operation_event_spool_flush_interval_seconds: float = Field(
        default=0.1, ge=0.01, le=5.0
    )
    http_responses_session_bridge_operation_event_spool_max_pending_events: int = Field(default=2048, gt=0)
    http_responses_session_bridge_operation_event_spool_max_pending_bytes: int = Field(default=32 * 1024 * 1024, gt=0)
    http_responses_session_bridge_operation_spool_retention_seconds: float = Field(default=7 * 24 * 60 * 60, gt=0)
    http_responses_session_bridge_ambiguous_continuation_recovery_mode: Literal[
        "fail_closed", "client_full_history_once", "server_anchored_replay_once", "server_indefinite_recovery"
    ] = "fail_closed"
    upstream_route_cache_ttl_seconds: float = Field(default=60.0, ge=0)
    request_log_retention_days: int = Field(default=0, ge=0, le=3650)
    usage_history_retention_days: int = Field(default=0, ge=0, le=3650)
    automations_scheduler_enabled: bool = True
    telemetry_enabled: bool | None = None
    telemetry_endpoint: str = "https://telemetry.tokmaxxing.com"
    encryption_key_fingerprint_mode: Literal["enforce", "warn", "off"] = "enforce"
    trace: str = ""
    model_registry_snapshot_max_age_seconds: int = Field(default=86400, gt=0)
    dashboard_trust_loopback_host_header_for_long_sessions: bool = False
    proxy_account_stream_recovery_reserve: int = Field(default=1, ge=0)
    proxy_api_key_fair_share_congestion_threshold_pct: int = Field(default=0, ge=0, le=100)
    proxy_account_caps_scope: Literal["partitioned", "replica"] = "partitioned"
    proxy_account_cap_partition_scale_down_seconds: int = Field(default=60, ge=30)
    workers_per_instance: int = Field(default=1, ge=1)
    timeout_invariant_validation_strict: bool = False
    event_loop_lag_warn_threshold_seconds: float = Field(default=0.5, ge=0.0)
