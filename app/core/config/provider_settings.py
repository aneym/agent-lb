from __future__ import annotations

from pydantic import Field

from app.core.config.settings_runtime_fields import SettingsRuntimeFields


class ProviderSettingsFields(SettingsRuntimeFields):
    anthropic_first_byte_timeout_seconds: float = Field(default=180.0, ge=0)
    anthropic_stream_idle_timeout_seconds: float = Field(default=300.0, ge=0)
    anthropic_frame_account_id: str = "2c436b54-a7e2-4299-9d6b-689ad2dda8cb"
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
    anthropic_helper_cache_1h_enabled: bool = True
    anthropic_claude_code_identity_enabled: bool = True
    anthropic_fable_routing_enabled: bool = True
    anthropic_fable_weekly_max_used_percent: float = Field(default=50.0, ge=0, le=100)
    anthropic_fable_sticky_drain_enabled: bool = True
    anthropic_fable_burn_first_enabled: bool = False
    anthropic_fable_over_threshold_probe_enabled: bool = True
    anthropic_fable_probe_ttl_seconds: int = 43200
    anthropic_fable_scoped_max_used_percent: float = Field(default=100.0, ge=0, le=100)
    anthropic_sticky_headroom_reallocation_enabled: bool = True
    anthropic_sticky_hold_until_exhausted: bool = True
    anthropic_route_to_extra_usage: bool = False
    anthropic_pool_exhausted_wait_enabled: bool = False
    anthropic_pool_exhausted_wait_max_seconds: float = Field(default=21600.0, gt=0)
