from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config.error_rule_schema import ErrorRule
from app.core.config.settings_defaults import (
    DEFAULT_CONVERSATION_ARCHIVE_DIR,
    DEFAULT_DATABASE_URL,
    DEFAULT_ENCRYPTION_KEY_FILE,
    DEFAULT_HOME_DIR,
    ENV_FILES,
    _configured_outbound_proxy_env,
    _default_home_dir,
    _default_http_bridge_instance_id,
    _default_local_instance_id,
    _default_oauth_callback_host,
    _default_upstream_websocket_trust_env,
)


class SettingsFields(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_LB_",
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default_factory=_default_home_dir)
    database_url: str = DEFAULT_DATABASE_URL
    database_pool_size: int = Field(default=15, gt=0)
    database_max_overflow: int = Field(default=10, ge=0)
    database_background_pool_size: int | None = Field(default=None, gt=0)
    database_background_max_overflow: int | None = Field(default=None, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_pool_recycle_seconds: int = Field(default=1800, gt=0)
    database_migrate_on_startup: bool = True
    database_validate_schema_drift_on_startup: bool = False
    database_sqlite_pre_migrate_backup_enabled: bool = True
    database_sqlite_pre_migrate_backup_max_files: int = Field(default=5, ge=1)
    database_sqlite_startup_check_mode: Literal["quick", "full", "off"] = "quick"
    upstream_base_url: str = "https://chatgpt.com/backend-api"
    upstream_stream_transport: Literal["http", "websocket", "auto"] = "auto"
    upstream_connect_timeout_seconds: float = 8.0
    upstream_connect_attempts: int = Field(default=3, ge=1)

    upstream_request_gzip_enabled: bool = True
    upstream_compact_timeout_seconds: float | None = None
    upstream_websocket_trust_env: bool = Field(default_factory=_default_upstream_websocket_trust_env)
    proxy_request_budget_seconds: float = Field(default=600.0, gt=0)
    http_responses_stream_request_budget_seconds: float = Field(default=7200.0, gt=0)
    compact_request_budget_seconds: float = Field(default=180.0, gt=0)
    stream_idle_timeout_seconds: float = 600.0

    anthropic_first_byte_timeout_seconds: float = Field(default=180.0, ge=0)
    anthropic_stream_idle_timeout_seconds: float = Field(default=300.0, ge=0)
    sse_keepalive_interval_seconds: float = Field(default=10.0, ge=0)
    anthropic_frame_account_id: str = "2c436b54-a7e2-4299-9d6b-689ad2dda8cb"
    proxy_downstream_websocket_idle_timeout_seconds: float = Field(default=120.0, gt=0)

    max_sse_event_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    upstream_response_create_max_bytes: int = Field(default=15 * 1024 * 1024, gt=0)
    auth_base_url: str = "https://auth.openai.com"
    oauth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    oauth_originator: str = "codex_chatgpt_desktop"
    oauth_scope: str = "openid profile email"
    oauth_timeout_seconds: float = 30.0
    oauth_redirect_uri: str = "http://localhost:1455/auth/callback"
    oauth_callback_host: str = _default_oauth_callback_host()
    oauth_callback_port: int = 1455
    anthropic_upstream_base_url: str = "https://api.anthropic.com"
    anthropic_auth_base_url: str = "https://platform.claude.com"
    anthropic_oauth_authorize_url: str = "https://claude.com/cai/oauth/authorize"
    anthropic_oauth_token_url: str = "https://platform.claude.com/v1/oauth/token"
    anthropic_oauth_client_id: str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    anthropic_oauth_scope: str = (
        "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
    )
    anthropic_oauth_refresh_scope: str = (
        "user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
    )
    anthropic_oauth_redirect_uri: str = "https://platform.claude.com/oauth/code/callback"
    anthropic_version: str = "2023-06-01"
    glm_anthropic_upstream_base_url: str = "https://api.z.ai/api/anthropic"
    kimi_anthropic_upstream_base_url: str = "https://api.kimi.com/coding"
    kimi_oauth_token_url: str = "https://auth.kimi.com/api/oauth/token"
    kimi_oauth_client_id: str = "17e5f671-d194-4dfb-9706-5516cb48c098"
    token_refresh_timeout_seconds: float = 8.0
    exchange_auto_reset_max_attempts: int = 3
    exchange_auto_reset_base_seconds: float = 900.0
    exchange_auto_reset_providers: tuple[str, ...] = ("anthropic",)
    auth_guardian_enabled: bool = False
    auth_guardian_interval_seconds: int = Field(default=21600, gt=0)
    auth_guardian_max_refresh_age_seconds: int = Field(default=43200, gt=0)
    auth_guardian_batch_size: int = Field(default=100, gt=0)
    auth_guardian_concurrency: int = Field(default=3, gt=0)
    auth_guardian_jitter_seconds: float = Field(default=300.0, ge=0)
    auth_guardian_failure_backoff_base_seconds: float = Field(default=300.0, ge=0)
    auth_guardian_failure_backoff_max_seconds: float = Field(default=3600.0, ge=0)

    anthropic_helper_cache_1h_enabled: bool = True
    anthropic_fable_routing_enabled: bool = True
    anthropic_fable_weekly_max_used_percent: float = Field(default=50.0, ge=0, le=100)

    anthropic_fable_sticky_drain_enabled: bool = True

    anthropic_fable_burn_first_enabled: bool = False

    anthropic_fable_over_threshold_probe_enabled: bool = True

    anthropic_fable_probe_ttl_seconds: int = 43200

    anthropic_fable_scoped_max_used_percent: float = Field(default=100.0, ge=0, le=100)

    anthropic_sticky_headroom_reallocation_enabled: bool = True

    anthropic_sticky_hold_until_exhausted: bool = True

    openai_sticky_hold_until_exhausted: bool = True

    anthropic_route_to_extra_usage: bool = False

    openai_route_to_credits: bool = False

    refuse_untagged_headless_opus: bool = False

    anthropic_pool_exhausted_wait_enabled: bool = False
    anthropic_pool_exhausted_wait_max_seconds: float = Field(default=21600.0, gt=0)
    account_pulse_enabled: bool = True
    account_pulse_interval_seconds: int = Field(default=21600, gt=0)
    account_pulse_recovery_interval_seconds: int = Field(default=900, gt=0)
    account_pulse_concurrency: int = Field(default=2, gt=0)
    account_pulse_jitter_seconds: float = Field(default=300.0, ge=0)
    account_pulse_failure_backoff_base_seconds: float = Field(default=600.0, ge=0)
    account_pulse_failure_backoff_max_seconds: float = Field(default=21600.0, ge=0)
    transcription_request_budget_seconds: float = Field(default=120.0, gt=0)
    token_refresh_interval_days: int = 8
    usage_fetch_timeout_seconds: float = 10.0
    usage_fetch_max_retries: int = 2
    usage_refresh_enabled: bool = True
    usage_refresh_interval_seconds: int = Field(default=60, gt=0)
    reset_credit_auto_redeem_enabled: bool = True
    reset_credit_auto_redeem_interval_seconds: int = Field(default=60, gt=0)

    reset_credit_auto_redeem_max_per_day: int = Field(default=2, ge=0)
    reset_credit_auto_redeem_cooldown_seconds: int = Field(default=900, ge=0)
    reset_credit_expiry_redeem_enabled: bool = True
    reset_credit_expiry_redeem_window_hours: int = Field(default=24, gt=0)
    reset_credit_expiry_sweep_interval_seconds: int = Field(default=3600, gt=0)

    public_usage_enabled: bool = True
    openai_cache_affinity_max_age_seconds: int = Field(default=1800, gt=0)
    warmup_model: str = "gpt-5.4-mini"
    openai_prompt_cache_key_derivation_enabled: bool = True
    http_responses_session_bridge_enabled: bool = True
    http_responses_session_bridge_request_budget_seconds: float = Field(default=7200.0, gt=0)

    http_bridge_first_event_timeout_seconds: float = Field(default=60.0, gt=0)

    http_bridge_strip_foreign_encrypted_reasoning: bool = True
    http_responses_session_bridge_idle_ttl_seconds: float = Field(default=120.0, gt=0)
    http_responses_session_bridge_codex_idle_ttl_seconds: float = Field(default=900.0, gt=0)
    http_responses_session_bridge_codex_prewarm_enabled: bool = False
    http_responses_session_bridge_max_sessions: int = Field(default=1024, gt=0)
    http_responses_session_bridge_queue_limit: int = Field(default=64, gt=0)
    http_responses_session_bridge_response_create_concurrency: int = Field(default=64, gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool = False
    http_responses_session_bridge_instance_id: str = Field(default_factory=_default_http_bridge_instance_id)

    local_instance_id: str = Field(default_factory=_default_local_instance_id)

    federation_token: str | None = None
    federation_mirror_token: str | None = None
    federation_transfer_token: str | None = None
    federation_transfer_inbound_sha256: str | None = None
    federation_transfer_outbound_token: str | None = None
    federation_taker_instance_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @property
    def effective_federation_mirror_token(self) -> str | None:
        return self.federation_mirror_token or self.federation_token

    federation_peer_url: str | None = None
    federation_mirror_interval_seconds: int = Field(default=300, gt=0)

    federation_mirror_include_pushed: bool = False
    federation_usage_window_days: int = Field(default=7, gt=0)

    federation_forward_request_logs: bool = True

    federation_push_path: Path = DEFAULT_HOME_DIR / "federation-push.json"
    federation_push_interval_seconds: int = Field(default=300, gt=0)
    federation_push_sources_path: Path = DEFAULT_HOME_DIR / "federation-push-sources.json"
    federation_tailscale_bin: str | None = None
    http_responses_session_bridge_instance_ring: Annotated[list[str], NoDecode] = Field(default_factory=list)
    http_responses_session_bridge_advertise_base_url: str | None = None
    accounts_cache_warmer_enabled: bool = True
    accounts_cache_warmer_interval_seconds: float = Field(default=10.0, gt=0)
    accounts_cache_warmer_startup_timeout_seconds: float = Field(default=30.0, gt=0)
    sticky_session_cleanup_enabled: bool = True
    sticky_session_cleanup_interval_seconds: int = Field(default=300, gt=0)
    quota_planner_scheduler_enabled: bool = True
    quota_planner_tick_seconds: int = Field(default=300, gt=0)
    encryption_key_file: Path = DEFAULT_ENCRYPTION_KEY_FILE
    database_migrations_fail_fast: bool = True
    log_proxy_request_shape: bool = False
    log_proxy_request_shape_raw_cache_key: bool = False
    log_proxy_request_payload: bool = False
    log_proxy_service_tier_trace: bool = False
    log_upstream_request_summary: bool = False
    log_upstream_request_payload: bool = False
    conversation_archive_enabled: bool = False
    conversation_archive_dir: Path = DEFAULT_CONVERSATION_ARCHIVE_DIR
    conversation_archive_queue_max_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    max_decompressed_body_bytes: int = Field(default=32 * 1024 * 1024, gt=0)
    max_decompressed_responses_body_bytes: int = Field(default=128 * 1024 * 1024, gt=0)
    image_inline_fetch_enabled: bool = True
    image_inline_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    images_host_model: str = "gpt-5.5"
    images_default_model: str = "gpt-image-2"
    images_max_partial_images: int = Field(default=3, ge=0, le=3)

    model_aliases: dict[str, str] = Field(default_factory=dict)
    excluded_model_patterns: list[str] = Field(default_factory=list)

    model_registry_enabled: bool = True
    model_registry_refresh_interval_seconds: int = Field(default=300, gt=0)
    model_registry_client_version: str = "0.101.0"
    model_context_window_overrides: Annotated[dict[str, int], NoDecode] = Field(default_factory=dict)
    proxy_unauthenticated_client_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)

    identity_enabled: bool = True
    factory_usage_token: str = ""
    identity_owner: str = "owner"
    identity_owner_machines: Annotated[list[str], NoDecode] = Field(default_factory=list)
    identity_local_machine: str | None = None
    identity_machine_aliases: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)
    identity_tailscale_bin: str = "tailscale"
    firewall_trust_proxy_headers: bool = False
    firewall_trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )
    firewall_ip_cache_ttl_seconds: int = Field(default=30, gt=0)
    dashboard_auth_mode: DashboardAuthMode = DashboardAuthMode.STANDARD

    def upstream_websocket_proxy_env(self) -> Mapping[str, str | None]:
        return _configured_outbound_proxy_env()

    dashboard_auth_proxy_header: str = "Remote-User"

    metrics_enabled: bool = False
    metrics_host: str = "127.0.0.1"
    metrics_port: int = 9090
    event_loop_lag_warning_threshold_seconds: float = 0.5

    log_format: str = "text"

    leader_election_enabled: bool = False
    leader_election_ttl_seconds: int = 600

    circuit_breaker_enabled: bool = False
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout_seconds: int = 60

    upstream_error_rules: tuple[ErrorRule, ...] = ()

    soft_drain_enabled: bool = True
    deterministic_failover_enabled: bool = True
    drain_primary_threshold_pct: float = 85.0
    drain_secondary_threshold_pct: float = 90.0
    drain_error_window_seconds: float = 60.0
    drain_error_count_threshold: int = 2
    model_account_exclusions: dict[str, dict[str, float]] = {
        "gpt-6.1-sol": {
            "2661c1c1-4689-49c6-88e8-6a342fd039d4_b419c00b": 1792680000,
            "409a8db4-52d6-4878-b305-7c60f96cdb0c_dbb5c4ac": 1792680000,
        }
    }
    probe_quiet_seconds: float = 60.0
    probe_success_streak_required: int = 3

    backpressure_max_concurrent_requests: int = 0

    bulkhead_proxy_limit: int = Field(default=1024, ge=0)
    bulkhead_proxy_http_limit: int | None = Field(default=None, ge=0)
    bulkhead_proxy_websocket_limit: int | None = Field(default=None, ge=0)
    bulkhead_proxy_compact_limit: int | None = Field(default=None, ge=0)
    bulkhead_dashboard_limit: int = Field(default=50, ge=0)
    dashboard_bootstrap_token: str | None = None
    proxy_token_refresh_limit: int = Field(default=64, ge=0)
    proxy_upstream_websocket_connect_limit: int = Field(default=512, ge=0)
    proxy_response_create_limit: int = Field(default=1024, ge=0)
    proxy_compact_response_create_limit: int = Field(default=128, ge=0)
    proxy_admission_wait_timeout_seconds: float = Field(default=10.0, gt=0)
    proxy_account_response_create_limit: int = Field(default=0, ge=0)
    proxy_account_stream_limit: int = Field(default=0, ge=0)
    proxy_account_inflight_penalty_pct: float = Field(default=2.5, ge=0)
    proxy_account_lease_token_weight: float = Field(default=1.0, ge=0)
    proxy_account_lease_ttl_seconds: float = Field(default=900.0, gt=0)
    proxy_refresh_failure_cooldown_seconds: float = Field(default=5.0, ge=0.0)
    usage_refresh_auth_failure_cooldown_seconds: float = Field(default=300.0, ge=0.0)

    memory_warning_threshold_mb: int = 0
    memory_reject_threshold_mb: int = 0

    otel_enabled: bool = False
    otel_exporter_endpoint: str = ""

    shutdown_drain_timeout_seconds: int = 30

    http_connector_limit: int = 1024
    http_connector_limit_per_host: int = 0
