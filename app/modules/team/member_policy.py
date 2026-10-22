from __future__ import annotations

import re

from app.db.access_models import TeamMember, TeamMemberStatus
from app.modules.proxy.request_policy import resolve_model_alias, strip_model_alias_suffix
from app.modules.team.pool_share import PoolWindow
from app.modules.team.repository import TeamUsageTotals
from app.modules.team.windows import TEAM_WINDOWS

_NEAR_CAP_RATIO = 0.8

GATE_OK = "ok"
GATE_NEAR_CAP = "near_cap"
GATE_OVER_CAP = "over_cap"
GATE_SUSPENDED = "suspended"


def cap_for(member: TeamMember, window: str, kind: str) -> float | int | None:
    return getattr(member, f"{kind}_cap_{window}" + ("_usd" if kind == "cost" else ""), None)


def status_value(status: object) -> str:
    if isinstance(status, TeamMemberStatus):
        return status.value
    return str(status)


def compute_gate(
    member: TeamMember, usage: dict[str, TeamUsageTotals], pool_windows: list[PoolWindow] | None = None
) -> str:
    if status_value(member.status) == TeamMemberStatus.SUSPENDED.value:
        return GATE_SUSPENDED

    near = False
    for window in TEAM_WINDOWS:
        totals = usage.get(window)
        if totals is None:
            continue
        cost_cap = cap_for(member, window, "cost")
        token_cap = cap_for(member, window, "token")
        if cost_cap is not None and float(cost_cap) > 0:
            ratio = totals.cost_usd / float(cost_cap)
            if ratio >= 1.0:
                return GATE_OVER_CAP
            if ratio >= _NEAR_CAP_RATIO:
                near = True
        if token_cap is not None and int(token_cap) > 0:
            ratio = totals.tokens / int(token_cap)
            if ratio >= 1.0:
                return GATE_OVER_CAP
            if ratio >= _NEAR_CAP_RATIO:
                near = True
    for pool_window in pool_windows or []:
        if pool_window.used_percent >= pool_window.limit_percent:
            return GATE_OVER_CAP
        if pool_window.used_percent >= _NEAR_CAP_RATIO * pool_window.limit_percent:
            near = True
    return GATE_NEAR_CAP if near else GATE_OK


_MODEL_CONTEXT_SUFFIX = re.compile(r"(?:\[[^\]]*\]|-1m)$")


def _model_match_keys(model: str) -> set[str]:

    normalized = model.strip().lower()
    if not normalized:
        return set()
    keys = {normalized, _MODEL_CONTEXT_SUFFIX.sub("", normalized)}
    for key in tuple(keys):
        alias = resolve_model_alias(key)
        if alias:
            keys.add(alias.strip().lower())
        keys.add(strip_model_alias_suffix(key))
    keys.discard("")
    return keys


def model_is_allowed(model: str, allowed_models: list[str]) -> bool:
    allowed: set[str] = set()
    for entry in allowed_models:
        allowed |= _model_match_keys(entry)
    return bool(_model_match_keys(model) & allowed)
