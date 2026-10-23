from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from pathlib import Path

from pydantic import field_validator, model_validator

from app.core.config.settings_fields import SettingsFields
from app.core.config.settings_validation import OptionalStringInput, StringListInput, identity_handle, logger


class SettingsIdentityValidators(SettingsFields):
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

    @field_validator("identity_owner_machines", mode="before")
    @classmethod
    def _normalize_identity_owner_machines(cls, value: StringListInput) -> list[str]:
        entries = value.split(",") if isinstance(value, str) else value or []
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
        if isinstance(value, list) and all((isinstance(item, str) for item in value)):
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

    @model_validator(mode="after")
    def _validate_transfer_credentials(self) -> "SettingsIdentityValidators":
        if self.federation_transfer_token is not None:
            raise ValueError(
                "federation_transfer_token is obsolete; use federation_transfer_inbound_sha256 and federation_transfer_outbound_token"
            )
        inbound = self.federation_transfer_inbound_sha256
        outbound = self.federation_transfer_outbound_token
        if inbound is not None and (len(inbound) != 64 or any((c not in "0123456789abcdef" for c in inbound))):
            raise ValueError("federation_transfer_inbound_sha256 must be 64 lowercase hex characters")
        other = [value for value in (self.federation_token, self.federation_mirror_token) if value]
        if outbound and any((hmac.compare_digest(outbound, value) for value in other)):
            raise ValueError("federation_transfer_outbound_token must differ from mirror and legacy credentials")
        if inbound and any(
            (
                hmac.compare_digest(inbound, hashlib.sha256(value.encode()).hexdigest())
                for value in ([outbound] if outbound else []) + other
            )
        ):
            raise ValueError(
                "federation_transfer_inbound_sha256 must differ from outbound, mirror and legacy credentials"
            )
        return self
