from __future__ import annotations

from typing import Literal


def stale_ttl_seconds(kind: Literal["response_create", "stream"], settings: object) -> float:
    ttl_seconds = float(getattr(settings, "proxy_account_lease_ttl_seconds", 900.0))
    if kind != "stream":
        return ttl_seconds
    valid_stream_budget_seconds = max(
        ttl_seconds,
        float(getattr(settings, "proxy_request_budget_seconds", ttl_seconds)),
        float(getattr(settings, "http_responses_stream_request_budget_seconds", ttl_seconds)),
        float(getattr(settings, "http_responses_session_bridge_request_budget_seconds", ttl_seconds)),
    )
    return max(ttl_seconds, valid_stream_budget_seconds + 60.0)
