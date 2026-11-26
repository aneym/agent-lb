from __future__ import annotations

from dataclasses import dataclass

from app.core.balancer import TRAFFIC_CLASS_FOREGROUND, TRAFFIC_CLASS_OPPORTUNISTIC, TrafficClass
from app.core.config.settings import get_settings
from app.modules.api_keys.service_data import ApiKeyData


@dataclass(frozen=True, slots=True)
class CallerSelectionPolicy:
    scoped_account_ids: set[str] | None
    traffic_class: TrafficClass
    allow_personal_fallback: bool
    is_unauthenticated: bool = False


def resolve_caller_selection_policy(
    api_key: ApiKeyData | None,
    *,
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
) -> CallerSelectionPolicy:
    app_settings = get_settings()
    scoped_account_ids = (
        set(api_key.assigned_account_ids) if api_key is not None and api_key.account_assignment_scope_enabled else None
    )
    if api_key is None:
        effective_traffic_class: TrafficClass = (
            TRAFFIC_CLASS_OPPORTUNISTIC
            if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC
            or app_settings.proxy_unauthenticated_default_traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC
            else TRAFFIC_CLASS_FOREGROUND
        )
    else:
        effective_traffic_class = (
            TRAFFIC_CLASS_OPPORTUNISTIC if api_key.traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC else traffic_class
        )
    allow_personal_fallback = not (
        app_settings.proxy_unauthenticated_exclude_personal
        and (api_key is None or effective_traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC)
    )
    return CallerSelectionPolicy(
        scoped_account_ids=scoped_account_ids,
        traffic_class=effective_traffic_class,
        allow_personal_fallback=allow_personal_fallback,
        is_unauthenticated=api_key is None,
    )
