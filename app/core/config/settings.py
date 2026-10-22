from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import socket
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator, model_validator

from app.core.auth.dashboard_mode import DashboardAuthMode, normalize_dashboard_auth_proxy_header
from app.core.config.settings_defaults import (
    DEFAULT_CONVERSATION_ARCHIVE_DIR,
    DEFAULT_DATABASE_URL,
    DEFAULT_ENCRYPTION_KEY_FILE,
    DEFAULT_HOME_DIR,
    _default_home_dir,
)
from app.core.config.settings_fields import SettingsFields

logger = logging.getLogger(__name__)

type StringListInput = str | list[str] | None
type OptionalStringInput = str | None
type ModelContextWindowOverridesInput = str | dict[str, int] | None


def identity_handle(value: str | None, limit: int) -> str | None:

    if not value:
        return None
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")[:limit].rstrip("-")
    return slug or None


def _validate_context_window_entries(data: Mapping[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for k, v in data.items():
        if isinstance(v, bool):
            raise TypeError(f"model_context_window_overrides value for '{k}' must be a positive integer, got bool")
        if not isinstance(v, int):
            raise TypeError(
                f"model_context_window_overrides value for '{k}' must be a positive integer, got {type(v).__name__}"
            )
        if v <= 0:
            raise ValueError(f"model_context_window_overrides value for '{k}' must be a positive integer, got {v}")
        result[str(k)] = v
    return result


def _parse_port_value(raw: str) -> int | None:
    try:
        port = int(raw)
    except ValueError:
        return None
    if port <= 0:
        return None
    return port


def _configured_http_port() -> int:
    raw_env_port = os.getenv("PORT")
    if raw_env_port is not None:
        parsed_env_port = _parse_port_value(raw_env_port.strip())
        if parsed_env_port is not None:
            return parsed_env_port
    return 2455


def _normalize_cidr_list(value: StringListInput, *, field_name: str, invalid_label: str) -> list[str]:
    if value is None:
        return []

    cidrs: list[str] = []
    if isinstance(value, str):
        entries = [entry.strip() for entry in value.split(",")]
        cidrs = [entry for entry in entries if entry]
    elif isinstance(value, list):
        for entry in value:
            if isinstance(entry, str):
                cidr = entry.strip()
                if cidr:
                    cidrs.append(cidr)
    else:
        raise TypeError(f"{field_name} must be a list or comma-separated string")

    for cidr in cidrs:
        try:
            ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid {invalid_label}: {cidr}") from exc
    return cidrs


class Settings(SettingsFields):
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

    @field_validator("federation_push_path", "federation_push_sources_path", mode="before")
    @classmethod
    def _expand_federation_push_paths(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value.strip()).expanduser()
        raise TypeError("federation push paths must be paths")

    @field_validator("federation_tailscale_bin", mode="before")
    @classmethod
    def _normalize_federation_tailscale_bin(cls, value: OptionalStringInput) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        raise TypeError("federation_tailscale_bin must be a string")

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

    @field_validator("identity_owner_machines", mode="before")
    @classmethod
    def _normalize_identity_owner_machines(cls, value: StringListInput) -> list[str]:
        entries = value.split(",") if isinstance(value, str) else (value or [])
        return [handle for entry in entries if (handle := identity_handle(entry, 48)) is not None]

    @field_validator("identity_machine_aliases", mode="before")
    @classmethod
    def _normalize_identity_machine_aliases(cls, value: str | Mapping[str, str] | None) -> dict[str, str]:

        if value is None:
            return {}
        if isinstance(value, str):
            pairs = [entry.partition("=") for entry in value.split(",") if entry.strip()]
            items = [(node, alias) for node, separator, alias in pairs if separator]
            if len(items) != len(pairs):
                logger.warning("Ignoring malformed identity machine alias entries")
        elif isinstance(value, Mapping):
            items = list(value.items())
        else:
            logger.warning("Ignoring malformed identity machine aliases")
            return {}
        result: dict[str, str] = {}
        for node, alias in items:
            node_key = identity_handle(str(node), 48)
            alias_value = identity_handle(str(alias), 48)
            if not node_key or not alias_value:
                logger.warning("Ignoring malformed identity machine alias entry")
                continue
            result[node_key] = alias_value
        return result

    @field_validator("firewall_trusted_proxy_cidrs", mode="before")
    @classmethod
    def _normalize_firewall_trusted_proxy_cidrs(cls, value: StringListInput) -> list[str]:
        return _normalize_cidr_list(
            value,
            field_name="firewall_trusted_proxy_cidrs",
            invalid_label="firewall trusted proxy CIDR",
        )

    @field_validator("proxy_unauthenticated_client_cidrs", mode="before")
    @classmethod
    def _normalize_proxy_unauthenticated_client_cidrs(cls, value: StringListInput) -> list[str]:
        return _normalize_cidr_list(
            value,
            field_name="proxy_unauthenticated_client_cidrs",
            invalid_label="proxy unauthenticated client CIDR",
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

    @field_validator("federation_peer_url", mode="before")
    @classmethod
    def _normalize_federation_peer_url(cls, value: OptionalStringInput) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip().rstrip("/")
            return stripped or None
        raise TypeError("federation_peer_url must be a string")

    @field_validator("federation_taker_instance_ids", mode="before")
    @classmethod
    def _normalize_federation_taker_instance_ids(cls, value: StringListInput) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return [item.strip() for item in value if item.strip()]
        raise TypeError("federation_taker_instance_ids must be a comma-separated string or list of strings")

    @field_validator(
        "federation_token",
        "federation_mirror_token",
        "federation_transfer_token",
        "federation_transfer_inbound_sha256",
        "federation_transfer_outbound_token",
        mode="before",
    )
    @classmethod
    def _normalize_federation_token(cls, value: OptionalStringInput) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        raise TypeError("federation_token must be a string")

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
    def _validate_transfer_credentials(self) -> "Settings":
        if self.federation_transfer_token is not None:
            raise ValueError(
                "federation_transfer_token is obsolete; use federation_transfer_inbound_sha256 "
                "and federation_transfer_outbound_token"
            )
        inbound = self.federation_transfer_inbound_sha256
        outbound = self.federation_transfer_outbound_token
        if inbound is not None and (len(inbound) != 64 or any(c not in "0123456789abcdef" for c in inbound)):
            raise ValueError("federation_transfer_inbound_sha256 must be 64 lowercase hex characters")
        other = [value for value in (self.federation_token, self.federation_mirror_token) if value]
        if outbound and any(hmac.compare_digest(outbound, value) for value in other):
            raise ValueError("federation_transfer_outbound_token must differ from mirror and legacy credentials")
        if inbound and any(
            hmac.compare_digest(inbound, hashlib.sha256(value.encode()).hexdigest())
            for value in ([outbound] if outbound else []) + other
        ):
            raise ValueError(
                "federation_transfer_inbound_sha256 must differ from outbound, mirror and legacy credentials"
            )
        return self

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
                "http_responses_session_bridge_instance_id must be explicitly present in "
                "http_responses_session_bridge_instance_ring"
            )
        advertise_base_url = self.http_responses_session_bridge_advertise_base_url
        if advertise_base_url is not None:
            hostname = urlparse(advertise_base_url).hostname
            if hostname is None:
                raise ValueError("http_responses_session_bridge_advertise_base_url must include a valid hostname")
            if not bridge_advertise_hostname_is_replica_specific(
                hostname,
                instance_id=self.http_responses_session_bridge_instance_id,
                multi_replica_intent=len(ring) > 1,
            ):
                raise ValueError(
                    "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
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
    def _validate_dashboard_auth_mode(self) -> "Settings":
        if self.dashboard_auth_mode != DashboardAuthMode.TRUSTED_HEADER:
            return self
        if not self.firewall_trust_proxy_headers:
            raise ValueError("dashboard_auth_mode=trusted_header requires firewall_trust_proxy_headers=true")
        if not self.firewall_trusted_proxy_cidrs:
            raise ValueError("dashboard_auth_mode=trusted_header requires non-empty firewall_trusted_proxy_cidrs")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def bridge_advertise_hostname_is_replica_specific(
    hostname: str,
    *,
    instance_id: str,
    multi_replica_intent: bool = False,
) -> bool:
    pod_ip = os.getenv("POD_IP")
    if pod_ip and hostname == pod_ip:
        return True
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        labels = set(hostname.split("."))
        pod_name = os.getenv("POD_NAME", "").strip()
        host_name = os.getenv("HOSTNAME", "").strip()
        allowed_labels = {
            label
            for label in {
                instance_id.strip(),
                pod_name,
                host_name,
                socket.gethostname().strip(),
            }
            if label
        }
        return bool(labels & allowed_labels)
    return parsed_ip.is_loopback and not multi_replica_intent
