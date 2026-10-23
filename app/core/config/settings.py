from __future__ import annotations

import json
from functools import cached_property, lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator, model_validator

from app.core.auth.dashboard_mode import DashboardAuthMode, normalize_dashboard_auth_proxy_header
from app.core.config.settings_defaults import (
    DEFAULT_CONVERSATION_ARCHIVE_DIR,
    DEFAULT_DATABASE_URL,
    DEFAULT_ENCRYPTION_KEY_FILE,
    _default_home_dir,
)
from app.core.config.settings_defaults import DEFAULT_HOME_DIR as DEFAULT_HOME_DIR
from app.core.config.settings_defaults import (
    bridge_advertise_hostname_is_replica_specific as bridge_advertise_hostname_is_replica_specific,
)
from app.core.config.settings_identity import SettingsIdentityValidators as SettingsIdentityValidators
from app.core.config.settings_validation import _REMOVED_SETTINGS as _REMOVED_SETTINGS
from app.core.config.settings_validation import AUTH_BASE_URL as AUTH_BASE_URL
from app.core.config.settings_validation import OAUTH_CALLBACK_PORT as OAUTH_CALLBACK_PORT
from app.core.config.settings_validation import OAUTH_CLIENT_ID as OAUTH_CLIENT_ID
from app.core.config.settings_validation import OAUTH_ORIGINATOR as OAUTH_ORIGINATOR
from app.core.config.settings_validation import OAUTH_REDIRECT_URI as OAUTH_REDIRECT_URI
from app.core.config.settings_validation import OAUTH_SCOPE as OAUTH_SCOPE
from app.core.config.settings_validation import ModelContextWindowOverridesInput as ModelContextWindowOverridesInput
from app.core.config.settings_validation import OptionalStringInput as OptionalStringInput
from app.core.config.settings_validation import StringListInput as StringListInput
from app.core.config.settings_validation import _configured_http_port as _configured_http_port
from app.core.config.settings_validation import _normalize_cidr_list as _normalize_cidr_list
from app.core.config.settings_validation import _parse_port_value as _parse_port_value
from app.core.config.settings_validation import _validate_context_window_entries as _validate_context_window_entries
from app.core.config.settings_validation import identity_handle as identity_handle
from app.core.config.settings_validation import logger as logger
from app.core.config.settings_validation import warn_removed_settings as warn_removed_settings


class Settings(SettingsIdentityValidators):
    @field_validator("request_log_retention_days")
    @classmethod
    def _validate_request_log_retention(cls, value: int) -> int:
        if value != 0 and value < 30:
            raise ValueError("request_log_retention_days must be 0 (disabled) or >= 30")
        return value

    @field_validator("usage_history_retention_days")
    @classmethod
    def _validate_usage_history_retention(cls, value: int) -> int:
        if value != 0 and value < 45:
            raise ValueError("usage_history_retention_days must be 0 (disabled) or >= 45")
        return value

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand_data_dir(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return _default_home_dir()
            return Path(stripped).expanduser()
        raise TypeError("data_dir must be a path")

    @field_validator("database_url")
    @classmethod
    def _expand_database_url(cls, value: str) -> str:
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if value.startswith(prefix):
                path = value[len(prefix) :]
                if path.startswith("~"):
                    return f"{prefix}{Path(path).expanduser()}"
        return value

    @field_validator("encryption_key_file", mode="before")
    @classmethod
    def _expand_encryption_key_file(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value).expanduser()
        raise TypeError("encryption_key_file must be a path")

    @field_validator("conversation_archive_dir", mode="before")
    @classmethod
    def _expand_conversation_archive_dir(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value).expanduser()
        raise TypeError("conversation_archive_dir must be a path")

    @field_validator("image_inline_allowed_hosts", mode="before")
    @classmethod
    def _normalize_image_inline_allowed_hosts(cls, value: StringListInput) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            entries = [entry.strip().lower().rstrip(".") for entry in value.split(",")]
            return [entry for entry in entries if entry]
        if isinstance(value, list):
            normalized: list[str] = []
            for entry in value:
                if isinstance(entry, str):
                    host = entry.strip().lower().rstrip(".")
                    if host:
                        normalized.append(host)
            return normalized
        raise TypeError("image_inline_allowed_hosts must be a list or comma-separated string")

    @field_validator("firewall_trusted_proxy_cidrs", mode="before")
    @classmethod
    def _normalize_firewall_trusted_proxy_cidrs(cls, value: StringListInput) -> list[str]:
        return _normalize_cidr_list(
            value, field_name="firewall_trusted_proxy_cidrs", invalid_label="firewall trusted proxy CIDR"
        )

    @field_validator("proxy_unauthenticated_client_cidrs", mode="before")
    @classmethod
    def _normalize_proxy_unauthenticated_client_cidrs(cls, value: StringListInput) -> list[str]:
        return _normalize_cidr_list(
            value, field_name="proxy_unauthenticated_client_cidrs", invalid_label="proxy unauthenticated client CIDR"
        )

    @field_validator("dashboard_auth_proxy_header", mode="before")
    @classmethod
    def _normalize_dashboard_auth_proxy_header(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("dashboard_auth_proxy_header must be a string")
        return normalize_dashboard_auth_proxy_header(value)

    @field_validator("http_responses_session_bridge_instance_ring", mode="before")
    @classmethod
    def _normalize_http_bridge_instance_ring(cls, value: StringListInput) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            entries = [entry.strip() for entry in value.split(",")]
            return [entry for entry in entries if entry]
        if isinstance(value, list):
            normalized: list[str] = []
            for entry in value:
                if isinstance(entry, str):
                    instance_id = entry.strip()
                    if instance_id:
                        normalized.append(instance_id)
            return normalized
        raise TypeError("http_responses_session_bridge_instance_ring must be a list or comma-separated string")

    @field_validator("http_responses_session_bridge_advertise_base_url", mode="before")
    @classmethod
    def _normalize_http_bridge_advertise_base_url(cls, value: OptionalStringInput) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip().rstrip("/")
            return stripped or None
        raise TypeError("http_responses_session_bridge_advertise_base_url must be a string")

    @field_validator("model_context_window_overrides", mode="before")
    @classmethod
    def _parse_model_context_window_overrides(cls, value: ModelContextWindowOverridesInput) -> dict[str, int]:
        if value is None:
            return {}
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return {}
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise TypeError("model_context_window_overrides must be a JSON object")
            return _validate_context_window_entries(parsed)
        if isinstance(value, dict):
            return _validate_context_window_entries(value)
        raise TypeError("model_context_window_overrides must be a JSON object string or dict")

    @field_validator("upstream_compact_timeout_seconds")
    @classmethod
    def _validate_upstream_compact_timeout_seconds(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("upstream_compact_timeout_seconds must be greater than zero")
        return value

    @field_validator("warmup_model", mode="before")
    @classmethod
    def _normalize_warmup_model(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("warmup_model must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("warmup_model must not be blank")
        return normalized

    @model_validator(mode="after")
    def _apply_data_dir_defaults(self) -> "Settings":
        if self.data_dir == DEFAULT_HOME_DIR:
            return self
        explicitly_set = self.model_fields_set
        if "database_url" not in explicitly_set and self.database_url == DEFAULT_DATABASE_URL:
            self.database_url = f"sqlite+aiosqlite:///{self.data_dir / 'store.db'}"
        if "encryption_key_file" not in explicitly_set and self.encryption_key_file == DEFAULT_ENCRYPTION_KEY_FILE:
            self.encryption_key_file = self.data_dir / "encryption.key"
        if (
            "conversation_archive_dir" not in explicitly_set
            and self.conversation_archive_dir == DEFAULT_CONVERSATION_ARCHIVE_DIR
        ):
            self.conversation_archive_dir = self.data_dir / "conversation-archive"
        return self

    @model_validator(mode="after")
    def _validate_http_bridge_instance_configuration(self) -> "Settings":
        ring = self.http_responses_session_bridge_instance_ring
        if ring and self.http_responses_session_bridge_instance_id not in ring:
            raise ValueError(
                "http_responses_session_bridge_instance_id must be explicitly present in http_responses_session_bridge_instance_ring"
            )
        advertise_base_url = self.http_responses_session_bridge_advertise_base_url
        if advertise_base_url is not None:
            hostname = urlparse(advertise_base_url).hostname
            if hostname is None:
                raise ValueError("http_responses_session_bridge_advertise_base_url must include a valid hostname")
            if not bridge_advertise_hostname_is_replica_specific(
                hostname, instance_id=self.http_responses_session_bridge_instance_id, multi_replica_intent=len(ring) > 1
            ):
                raise ValueError(
                    "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
                )
        return self

    @cached_property
    def trace_channels(self) -> frozenset[str]:
        return frozenset((entry.strip().lower() for entry in self.trace.split(",") if entry.strip()))

    @model_validator(mode="after")
    def _validate_token_refresh_claim_ttl(self) -> "Settings":
        minimum_ttl = self.proxy_admission_wait_timeout_seconds + 2.0 * self.token_refresh_timeout_seconds
        if "token_refresh_claim_ttl_seconds" not in self.model_fields_set:
            self.token_refresh_claim_ttl_seconds = max(self.token_refresh_claim_ttl_seconds, minimum_ttl)
            return self
        if self.token_refresh_claim_ttl_seconds < minimum_ttl:
            raise ValueError(
                f"token_refresh_claim_ttl_seconds must be at least proxy_admission_wait_timeout_seconds + 2x token_refresh_timeout_seconds ({minimum_ttl}s) so a healthy claimant cannot lose its claim while waiting for refresh admission or mid-exchange"
            )
        return self

    @model_validator(mode="after")
    def _normalize_bulkhead_limits(self) -> "Settings":
        if self.bulkhead_proxy_http_limit is None:
            self.bulkhead_proxy_http_limit = self.bulkhead_proxy_limit
        if self.bulkhead_proxy_websocket_limit is None:
            self.bulkhead_proxy_websocket_limit = self.bulkhead_proxy_limit
        if self.bulkhead_proxy_compact_limit is None:
            http_limit = self.bulkhead_proxy_http_limit
            self.bulkhead_proxy_compact_limit = 0 if http_limit <= 0 else min(http_limit, 16)
        return self

    @model_validator(mode="after")
    def _validate_metrics_port(self) -> "Settings":
        http_port = _configured_http_port()
        if self.metrics_port == http_port:
            raise ValueError(f"metrics_port must not match the main application port ({http_port})")
        return self

    @model_validator(mode="after")
    def _validate_firewall_proxy_trust(self) -> "Settings":
        if self.firewall_trust_proxy_headers and (not self.firewall_trusted_proxy_cidrs):
            raise ValueError(
                "firewall_trust_proxy_headers=true requires at least one entry in firewall_trusted_proxy_cidrs; configure a trusted proxy CIDR or disable proxy header trust"
            )
        return self

    @model_validator(mode="after")
    def _validate_dashboard_auth_mode(self) -> "Settings":
        if self.dashboard_auth_mode != DashboardAuthMode.TRUSTED_HEADER:
            return self
        if not self.firewall_trust_proxy_headers:
            raise ValueError("dashboard_auth_mode=trusted_header requires firewall_trust_proxy_headers=true")
        return self

    @model_validator(mode="after")
    def _validate_workers_per_instance(self) -> "Settings":
        if self.workers_per_instance > 1:
            raise ValueError(
                f"workers_per_instance (CODEX_LB_WORKERS_PER_INSTANCE={self.workers_per_instance}) is not supported: running more than one worker process per instance would multiply per-account concurrency caps, because those caps are partitioned per replica via the bridge ring and intra-pod worker partitioning cannot be made reliable. Run ONE worker per pod/container and scale horizontally via replicas (the bridge ring partitions caps per replica); set CODEX_LB_WORKERS_PER_INSTANCE=1 (the default)."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
