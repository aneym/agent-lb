from __future__ import annotations

from collections.abc import Mapping

from app.core.anthropic.entrypoint_label import relabel_user_agent
from app.core.anthropic.oauth import ANTHROPIC_PROVIDER_NAME
from app.core.clients.proxy import filter_inbound_headers
from app.core.config.settings import get_settings

ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"
ANTHROPIC_FAST_MODE_BETA = "fast-mode-2026-02-01"


def build_anthropic_headers(
    inbound_headers: Mapping[str, str],
    access_token: str,
    *,
    provider_name: str = ANTHROPIC_PROVIDER_NAME,
    fast_mode: bool = False,
) -> dict[str, str]:
    settings = get_settings()
    headers = {
        key: value
        for key, value in filter_inbound_headers(inbound_headers).items()
        if key.lower() not in {"api-key", "x-api-key"}
    }
    lower_keys = {key.lower() for key in headers}
    headers["Authorization"] = f"Bearer {access_token}"
    if "anthropic-version" not in lower_keys:
        headers["anthropic-version"] = settings.anthropic_version
    if "content-type" not in lower_keys:
        headers["content-type"] = "application/json"
    if "accept" not in lower_keys:
        headers["accept"] = "text/event-stream"
    if provider_name == ANTHROPIC_PROVIDER_NAME:
        required_betas = [ANTHROPIC_OAUTH_BETA]
        if fast_mode:
            required_betas.append(ANTHROPIC_FAST_MODE_BETA)
        merge_anthropic_beta_header(headers, required_betas)
        for key, value in headers.items():
            if key.lower() == "user-agent":
                headers[key] = relabel_user_agent(value, settings.anthropic_entrypoint_label)
    return headers


def merge_anthropic_beta_header(headers: dict[str, str], required_betas: list[str]) -> None:
    beta_key = next((key for key in headers if key.lower() == "anthropic-beta"), "anthropic-beta")
    existing = headers.get(beta_key, "")
    merged: list[str] = []
    seen: set[str] = set()
    for raw_value in [*existing.split(","), *required_betas]:
        beta = raw_value.strip()
        if not beta:
            continue
        normalized = beta.lower()
        if normalized in seen:
            continue
        merged.append(beta)
        seen.add(normalized)
    if merged:
        headers[beta_key] = ", ".join(merged)
