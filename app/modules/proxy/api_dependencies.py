from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ProxyApiDependencies:
    anthropic_request_session_id: Callable[..., Any]
    is_fable_model: Callable[..., Any]
    rate_limit_details: Callable[..., Any]
    parse_trusted_proxy_networks: Callable[..., Any]


proxy_dependencies = ProxyApiDependencies()
