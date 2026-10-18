from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any


def _project_account(account: dict[str, Any]) -> dict[str, Any]:
    usage = account.get("usage") if isinstance(account.get("usage"), dict) else {}
    subscription = account.get("subscription") if isinstance(account.get("subscription"), dict) else None
    quotas = account.get("additionalQuotas") if isinstance(account.get("additionalQuotas"), list) else []
    projected_quotas = [_project_quota(item) for item in quotas if isinstance(item, dict)]
    status = _optional_text(account.get("status")) or "unknown"
    subscription_status = _optional_text(subscription.get("status")) if subscription else None
    usable, usability_reasons = _account_usability(status, subscription_status, account)
    fable = _fable_observation(account, projected_quotas)
    return {
        "account_id": _optional_text(account.get("accountId"))
        or _optional_text(account.get("account_id"))
        or "unknown",
        "provider": _text(account.get("provider"), "openai"),
        "status": status,
        "subscription": {
            "status": subscription_status,
            "usable": None if subscription_status is None else _subscription_usable(subscription_status),
        },
        "usable": usable,
        "usability_reasons": usability_reasons,
        "primary": _window_from_remaining(usage.get("primaryRemainingPercent"), account.get("resetAtPrimary")),
        "weekly": _window_from_remaining(usage.get("secondaryRemainingPercent"), account.get("resetAtSecondary")),
        "quota_cooldowns": _quota_cooldowns(projected_quotas),
        "rate_limit_reset_at": _optional_text(account.get("rateLimitResetAt")),
        "reset_credits_available": _reset_credits_available(account.get("resetCreditsAvailable")),
        "last_primed_at": _optional_text(account.get("lastPrimedAt")),
        "fable": fable,
        "quota_windows": projected_quotas,
    }


def _project_quota(quota: dict[str, Any]) -> dict[str, Any]:
    primary = quota.get("primaryWindow") if isinstance(quota.get("primaryWindow"), dict) else {}
    model_ids = quota.get("modelIds") if isinstance(quota.get("modelIds"), list) else []
    return {
        "quota_key": _optional_text(quota.get("quotaKey")) or _optional_text(quota.get("limitName")) or "unknown",
        "model_ids": [item for item in model_ids if isinstance(item, str)],
        "primary": _window_from_used(primary.get("usedPercent"), primary.get("resetAt")),
        "recorded_at": _optional_text(quota.get("recordedAt")),
    }


def _window_from_remaining(remaining: Any, reset_at: Any) -> dict[str, Any]:
    return {"remaining_percent": _number(remaining), "reset_at": _optional_text(reset_at)}


def _window_from_used(used: Any, reset_at: Any) -> dict[str, Any]:
    numeric = _number(used)
    return {
        "remaining_percent": None if numeric is None else max(0.0, 100.0 - numeric),
        "reset_at": _optional_text(reset_at),
    }


def _fable_observation(account: dict[str, Any], quotas: list[dict[str, Any]]) -> dict[str, Any]:
    raw_scoped = account.get("fableScopedWeekly")
    if not isinstance(raw_scoped, dict):
        raw_scoped = None
    quota = next((item for item in quotas if item["quota_key"] == "anthropic_fable_scoped_weekly"), None)
    policy = account.get("fableEligible")
    if not isinstance(policy, bool):
        policy = None
    if raw_scoped is None and quota is None:
        return {"routing_policy_eligible": policy, "scoped_weekly": None, "freshness": "unknown"}
    scoped = (
        _window_from_used(raw_scoped.get("usedPercent"), raw_scoped.get("resetAt"))
        if raw_scoped is not None
        else quota["primary"]
    )
    recorded_at = _optional_text(raw_scoped.get("recordedAt")) if raw_scoped else quota["recorded_at"]
    reported_fresh = raw_scoped.get("fresh") if raw_scoped else None
    return {
        "routing_policy_eligible": policy,
        "scoped_weekly": scoped,
        "freshness": "fresh"
        if reported_fresh is True
        else "stale"
        if reported_fresh is False
        else _freshness(recorded_at),
        "recorded_at": recorded_at,
    }


def _quota_cooldowns(quotas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"quota_key": quota["quota_key"], "reset_at": quota["primary"]["reset_at"]}
        for quota in quotas
        if quota["primary"]["remaining_percent"] == 0 and _future(quota["primary"]["reset_at"])
    ]


def _account_usability(status: str, subscription: str | None, account: dict[str, Any]) -> tuple[str, list[str]]:
    known_statuses = {
        "active",
        "rate_limited",
        "quota_exceeded",
        "paused",
        "reauth_required",
        "exchange_uncertain",
        "deactivated",
    }
    if status not in known_statuses:
        return "unknown", ["account status is unknown"]
    if status == "paused" and str(account.get("deactivationReason") or "").startswith("balance_exhausted:"):
        return "blocked", ["no balance"]
    if status != "active":
        return "blocked", [f"account status is {status}"]
    if not _subscription_usable(subscription):
        return "blocked", ["subscription is canceled"]
    if _future(_optional_text(account.get("rateLimitResetAt"))):
        return "blocked", ["account rate limit is cooling down"]
    usage = account.get("usage") if isinstance(account.get("usage"), dict) else None
    if usage is None:
        return "unknown", ["account usage telemetry is missing"]
    observed_windows = 0
    for key, reset_key in (
        ("primaryRemainingPercent", "resetAtPrimary"),
        ("secondaryRemainingPercent", "resetAtSecondary"),
    ):
        if usage.get(key) is None:
            continue
        remaining = _number(usage.get(key))
        if remaining is None:
            return "unknown", ["account usage telemetry is malformed"]
        observed_windows += 1
        if remaining == 0 and _future(_optional_text(account.get(reset_key))):
            return "blocked", [f"{key} is exhausted until its reset"]
    if not observed_windows:
        return "unknown", ["account usage telemetry is incomplete"]
    return "usable", []


def _model_observation(accounts: list[dict[str, Any]], model: str, thinking: bool) -> dict[str, Any]:
    inferred_provider = _model_provider(model)
    if inferred_provider is None:
        return {"status": "unknown", "reasons": ["model provider is not recognized"]}
    if not accounts:
        return {"status": "unknown", "reasons": ["no accounts matched the requested provider"]}
    fable_model = "fable" in model.casefold()
    accounts = [account for account in accounts if account["provider"].casefold() == inferred_provider]
    if not accounts:
        return {"status": "unknown", "reasons": [f"no {inferred_provider} accounts observed for this model"]}
    states: list[str] = []
    reasons: list[str] = []
    for account in accounts:
        if account["usable"] == "blocked":
            states.append("blocked")
            reasons.extend(account["usability_reasons"])
            continue
        if account["usable"] == "unknown":
            states.append("unknown")
            reasons.append("account usage telemetry is missing or incomplete")
            continue
        if fable_model:
            scoped = account["fable"]["scoped_weekly"]
            freshness = account["fable"]["freshness"]
            if freshness != "fresh":
                states.append("unknown")
                reasons.append(
                    "Fable scoped telemetry is stale"
                    if freshness == "stale"
                    else "Fable scoped telemetry freshness is unknown"
                )
            elif scoped and scoped["remaining_percent"] == 0 and _future(scoped["reset_at"]):
                states.append("blocked")
                reasons.append("observed Fable scoped weekly quota is exhausted until its reset")
            elif account["fable"]["routing_policy_eligible"] is False:
                states.append("unknown")
                reasons.append("fableEligible=false is current routing policy, not a server availability verdict")
            else:
                states.append("usable")
            if states[-1] != "blocked":
                quota_state, quota_reason = _anthropic_model_quota(account["quota_windows"], model, thinking)
                if quota_state == "blocked" or states[-1] == "usable" and quota_state == "unknown":
                    states[-1] = quota_state
                if quota_reason:
                    reasons.append(quota_reason)
        elif inferred_provider == "anthropic":
            quota_state, quota_reason = _anthropic_model_quota(account["quota_windows"], model, thinking)
            states.append(quota_state)
            if quota_reason:
                reasons.append(quota_reason)
        else:
            matching = [quota for quota in account["quota_windows"] if model in quota["model_ids"]]
            if any(_quota_exhausted(quota) for quota in matching):
                states.append("blocked")
                reasons.append("observed model quota is exhausted until its reset")
            else:
                states.append("usable")
    if "usable" in states:
        status = "usable"
    elif "unknown" in states:
        status = "unknown"
    else:
        status = "blocked"
    return {"status": status, "reasons": sorted(set(reasons))}


def _anthropic_model_quota(quotas: list[dict[str, Any]], model: str, thinking: bool) -> tuple[str, str | None]:
    opus_model = "opus" in model.casefold()
    if "haiku" in model.casefold():
        keys = {"anthropic_standard"}
    elif opus_model:
        keys = {"anthropic_opus_thinking"} if thinking else {"anthropic_opus"}
    else:
        keys = {"anthropic_top_thinking"} if thinking else {"anthropic_top", "anthropic_top_thinking"}
    relevant = [quota for quota in quotas if quota["quota_key"] in keys]
    if not relevant:
        return "unknown", "relevant Anthropic model quota telemetry is missing"
    exhausted = [_quota_exhausted(quota) for quota in relevant]
    if opus_model and any(exhausted):
        return "unknown", "observed Opus scoped quota is exhausted; routing uses bounded retries"
    if all(exhausted):
        return "blocked", "all observed relevant Anthropic model quotas are exhausted until reset"
    if any(exhausted):
        return "unknown", "thinking and non-thinking Anthropic quota states differ"
    if any(quota["primary"]["remaining_percent"] is None for quota in relevant):
        return "unknown", "relevant Anthropic model quota telemetry is incomplete"
    return "usable", None


def _quota_exhausted(quota: dict[str, Any]) -> bool:
    return quota["primary"]["remaining_percent"] == 0 and _future(quota["primary"]["reset_at"])


def _subscription_usable(status: str | None) -> bool:
    return (status or "").strip().casefold() != "canceled"


def _model_provider(model: str) -> str | None:
    normalized = model.casefold()
    if "fable" in normalized or "claude" in normalized or normalized in {"opus", "sonnet"}:
        return "anthropic"
    if normalized.startswith(("gpt", "o1", "o3", "o4", "codex")):
        return "openai"
    return None


def _provider_summaries(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    providers: dict[str, dict[str, int]] = {}
    for account in accounts:
        summary = providers.setdefault(account["provider"], {"accounts": 0, "usable": 0, "blocked": 0})
        summary["accounts"] += 1
        summary[account["usable"]] = summary.get(account["usable"], 0) + 1
    return [{"provider": name, **values} for name, values in sorted(providers.items())]


def _freshness(recorded_at: str | None) -> str:
    if not recorded_at:
        return "unknown"
    try:
        observed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return "unknown"
        return "fresh" if (datetime.now(UTC) - observed).total_seconds() <= 6 * 60 * 60 else "stale"
    except ValueError:
        return "unknown"


def _future(value: str | None) -> bool:
    if not value:
        return False
    try:
        if value.isdigit():
            return int(value) > int(datetime.now(UTC).timestamp())
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed > datetime.now(UTC)
    except ValueError:
        return False


def _number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and 0 <= numeric <= 100 else None


def _reset_credits_available(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_text(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str)
        else (str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None)
    )


def _text(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default
