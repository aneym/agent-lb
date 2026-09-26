"""Read-only local status observation for the agent-lb command line.

This module deliberately uses only the standard library.  Importing the normal
application pulls in the server and its configuration, which is both slow and
the wrong side effect for a command whose only job is to observe a running
local service.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SCHEMA_VERSION = 1
DEFAULT_BASE_URL = "http://127.0.0.1:2455"


class StatusObservationError(Exception):
    """A safe-to-display failure while observing the local service."""


def run(args: Any) -> None:
    """Observe the running service and exit nonzero only when observation fails."""
    try:
        payload = observe(
            base_url=args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL),
            timeout=args.timeout,
            provider=args.provider,
            model=args.model,
            thinking=args.thinking,
        )
    except StatusObservationError as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "observed_at": _now(),
            "health": {"state": "error"},
            "providers": [],
            "accounts": [],
            "error": {"kind": "observation_failed", "message": str(exc)},
        }
        _emit(payload, json_output=args.json)
        raise SystemExit(2) from exc

    _emit(payload, json_output=args.json)


def observe(
    *, base_url: str, timeout: float, provider: str | None, model: str | None, thinking: bool = False
) -> dict[str, Any]:
    base_url = _validated_base_url(base_url)
    health = _get_json(base_url + "/health/ready", timeout)
    accounts_response = _get_json(base_url + "/api/accounts", timeout)
    if not isinstance(health, dict) or not isinstance(accounts_response, dict):
        raise StatusObservationError("service returned a malformed status response")
    health_status = _optional_text(health.get("status"))
    if health_status not in {"ok", "ready"}:
        raise StatusObservationError("service did not report ready health")
    raw_accounts = accounts_response.get("accounts")
    if not isinstance(raw_accounts, list) or not all(isinstance(item, dict) for item in raw_accounts):
        raise StatusObservationError("service returned a malformed accounts response")

    selected_provider = provider.casefold() if provider else None
    accounts = [
        _project_account(account)
        for account in raw_accounts
        if selected_provider is None or _text(account.get("provider"), "openai").casefold() == selected_provider
    ]
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": _now(),
        "health": {"state": "ready", "reported_status": health_status},
        "providers": _provider_summaries(accounts),
        "accounts": accounts,
        "availability_note": "Snapshot only; it is not a dispatch reservation or availability guarantee.",
    }
    try:
        cache_summary = _get_json(base_url + "/api/request-logs/anthropic-cache-summary", timeout)
        result["anthropic_cache"] = _anthropic_cache_observation(cache_summary)
    except StatusObservationError:
        result["anthropic_cache"] = _unknown_anthropic_cache()
    if provider:
        result["provider_filter"] = provider
    if model:
        result["model"] = {"name": model, "thinking": thinking, **_model_observation(accounts, model, thinking)}
    return result


def _unknown_anthropic_cache() -> dict[str, Any]:
    return {"state": "unknown", "ratio_percent": None, "window_minutes": 60, "request_count": None}


def _anthropic_cache_observation(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict) or summary.get("window_minutes") != 60:
        return _unknown_anthropic_cache()
    values = [
        summary.get(key)
        for key in (
            "request_count",
            "incomplete_request_count",
            "input_tokens",
            "cache_creation_tokens",
            "cache_read_tokens",
        )
    ]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return _unknown_anthropic_cache()
    count, incomplete, uncached, creation, read = values
    observation = {"state": "unknown", "ratio_percent": None, "window_minutes": 60, "request_count": count}
    denominator = uncached + creation + read
    if not count or incomplete or incomplete > count or not denominator:
        return observation
    observation["ratio_percent"] = 100 * read / denominator
    observation["state"] = "alert" if 2 * read < denominator else "ok"
    return observation


def _validated_base_url(value: str) -> str:
    value = value.rstrip("/")
    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError as exc:
        raise StatusObservationError("--base-url must be a plain http(s) origin") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed_port is not None
        and not 0 < parsed_port <= 65535
    ):
        raise StatusObservationError("--base-url must be a plain http(s) origin")
    return value


def _get_json(url: str, timeout: float) -> Any:
    headers = {"Accept": "application/json"}
    # Supplying this is an explicit operator choice. Never print it or an HTTP body.
    if cookie := os.getenv("AGENT_LB_STATUS_COOKIE"):
        headers["Cookie"] = cookie
    request = Request(url, headers=headers, method="GET")
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=timeout) as response:  # noqa: S310 -- URL is explicit local/operator input.
            if response.status < 200 or response.status >= 300:
                raise StatusObservationError(f"service returned HTTP {response.status}")
            try:
                return json.loads(response.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StatusObservationError("service returned invalid JSON") from exc
    except HTTPError as exc:
        raise StatusObservationError(f"service returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise StatusObservationError("service is unavailable or did not respond") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> Request | None:
        return None


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
    known_statuses = {"active", "rate_limited", "quota_exceeded", "paused", "reauth_required", "deactivated"}
    if status not in known_statuses:
        return "unknown", ["account status is unknown"]
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
        # The accounts API reports a window the plan does not have as null
        # (ChatGPT Pro has no five-hour window); that is absent, not malformed.
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
    if "haiku" in model.casefold():
        keys = {"anthropic_standard"}
    else:
        keys = {"anthropic_top_thinking"} if thinking else {"anthropic_top", "anthropic_top_thinking"}
    relevant = [quota for quota in quotas if quota["quota_key"] in keys]
    if not relevant:
        return "unknown", "relevant Anthropic model quota telemetry is missing"
    exhausted = [_quota_exhausted(quota) for quota in relevant]
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


def _emit(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    print(f"agent-lb status: {payload['health']['state']} ({payload['observed_at']})")
    if error := payload.get("error"):
        print(f"error: {error['message']}", file=sys.stderr)
        return
    for provider in payload["providers"]:
        print(f"{provider['provider']}: {provider['usable']}/{provider['accounts']} usable")
    for account in payload["accounts"]:
        fable = (
            f"; Fable scoped weekly {_format_window(account['fable']['scoped_weekly'])}"
            if "fable" in payload.get("model", {}).get("name", "").casefold()
            else ""
        )
        print(
            f"  {account['account_id']} [{account['status']}/{account['usable']}]: "
            f"primary {_format_window(account['primary'])}; weekly {_format_window(account['weekly'])}; "
            f"banked resets {_format_reset_credits(account['reset_credits_available'])}; "
            f"last primed {account['last_primed_at'] or 'never'}{fable}"
        )
    if model := payload.get("model"):
        print(f"model {model['name']}: {model['status']}")
        for reason in model["reasons"]:
            print(f"  - {reason}")
    cache = payload.get("anthropic_cache")
    if cache:
        if cache["state"] == "alert":
            print(f"ALERT Anthropic cache-read ratio (last hour): {cache['ratio_percent']}% (<50%)")
        elif cache["state"] == "ok":
            print(f"Anthropic cache-read ratio (last hour): {cache['ratio_percent']}%")
        else:
            print("Anthropic cache-read ratio (last hour): unknown")
    print(payload["availability_note"])


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and 0 <= numeric <= 100 else None


def _reset_credits_available(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _format_reset_credits(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _format_window(window: dict[str, Any] | None) -> str:
    if not window or window["remaining_percent"] is None:
        return "unknown"
    remaining = window["remaining_percent"]
    display = int(remaining) if remaining.is_integer() else remaining
    reset = f" reset {window['reset_at']}" if window["reset_at"] else ""
    return f"{display}%{reset}"


def _optional_text(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str)
        else (str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None)
    )


def _text(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default
