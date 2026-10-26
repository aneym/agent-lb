from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from app.status_projection import _model_observation, _optional_text, _project_account, _provider_summaries, _text

SCHEMA_VERSION = 1


DEFAULT_BASE_URL = "http://127.0.0.1:2455"


STATUS_CACHE_MAX_AGE_SECONDS = 600


class StatusObservationError(Exception):
    pass


def run(args: Any) -> None:

    base_url = args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL)
    provider = args.provider
    model = args.model
    thinking = bool(args.thinking)
    try:
        payload = observe(
            base_url=base_url,
            timeout=args.timeout,
            provider=provider,
            model=model,
            thinking=thinking,
        )
    except StatusObservationError as exc:
        cached = _read_status_cache(base_url, provider, model, thinking)
        if cached is not None:
            snapshot, age_seconds = cached
            _emit_stale(snapshot, age_seconds=age_seconds, message=str(exc), json_output=args.json)
            return
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

    _write_status_cache(base_url, provider, model, thinking, payload)
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
    result["http_status"] = _optional_get(
        base_url + f"/api/request-logs/http-status-summary?window_minutes={HTTP_STATUS_WINDOW_MINUTES}", timeout
    )
    result["db_pool"] = _optional_get(base_url + "/health/db-pool", timeout)
    if provider:
        result["provider_filter"] = provider
    if model:
        result["model"] = {"name": model, "thinking": thinking, **_model_observation(accounts, model, thinking)}
    return result


HTTP_STATUS_WINDOW_MINUTES = 60


def _optional_get(url: str, timeout: float) -> Any:
    try:
        return _get_json(url, timeout)
    except StatusObservationError:
        return None


CACHE_ALERT_READ_PERCENT = 90


CACHE_ALERT_MIN_REQUESTS = 50


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
    observation["incomplete_request_count"] = incomplete
    if not count or incomplete > count or not denominator:
        return observation
    observation["ratio_percent"] = 100 * read / denominator
    if count < CACHE_ALERT_MIN_REQUESTS:
        observation["state"] = "ok"
    else:
        observation["state"] = "alert" if read * 100 < CACHE_ALERT_READ_PERCENT * denominator else "ok"
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


def _projected_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _cache_dir() -> Path:
    configured = os.getenv("AGENT_LB_STATUS_CACHE_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".agent-lb" / "state" / "status-cache"


def _status_cache_path(base_url: str, provider: str | None, model: str | None, thinking: bool) -> Path | None:
    try:
        normalized = _validated_base_url(base_url)
    except StatusObservationError:
        return None
    material = "|".join((normalized, provider or "", model or "", "true" if thinking else "false"))
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]
    return _cache_dir() / f"status-{digest}.json"


def _write_status_cache(
    base_url: str, provider: str | None, model: str | None, thinking: bool, payload: dict[str, Any]
) -> None:
    path = _status_cache_path(base_url, provider, model, thinking)
    if path is None:
        return
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd, temporary = tempfile.mkstemp(prefix=".status-", dir=path.parent)
        with os.fdopen(fd, "w") as stream:
            stream.write(_projected_json(payload) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    except Exception:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _read_status_cache(
    base_url: str, provider: str | None, model: str | None, thinking: bool
) -> tuple[dict[str, Any], int] | None:
    path = _status_cache_path(base_url, provider, model, thinking)
    if path is None:
        return None
    try:
        age_seconds = int(time.time() - path.stat().st_mtime)
        if age_seconds < 0 or age_seconds >= STATUS_CACHE_MAX_AGE_SECONDS:
            return None
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("observed_at"), str):
        return None
    return payload, age_seconds


def _emit_stale(snapshot: dict[str, Any], *, age_seconds: int, message: str, json_output: bool) -> None:
    if json_output:
        _emit(
            {
                **snapshot,
                "stale": True,
                "cached_at": snapshot["observed_at"],
                "cache_age_seconds": age_seconds,
                "error": {"kind": "observation_failed", "message": message},
            },
            json_output=True,
        )
        return
    print(f"agent-lb status: stale snapshot ({age_seconds}s old)")
    _emit(snapshot, json_output=False)


def _emit(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(_projected_json(payload))
        return
    print(f"agent-lb status: {payload['health']['state']} ({payload['observed_at']})")
    if error := payload.get("error"):
        print(f"error: {error['message']}", file=sys.stderr)
        return
    for provider in payload["providers"]:
        print(f"{provider['provider']}: {provider['usable']}/{provider['accounts']} usable")
    attention = [account for account in payload["accounts"] if account.get("problems")]
    if attention:
        print("needs attention:")
        for account in attention:
            for problem in account["problems"]:
                print(f"  {account['email']} ({account['provider']}) {problem['code']}: {problem['message']}")
    for account in payload["accounts"]:
        fable = (
            f"; Fable scoped weekly {_format_window(account['fable']['scoped_weekly'])}"
            if "fable" in payload.get("model", {}).get("name", "").casefold()
            else ""
        )
        status_label = "no balance" if "no balance" in account["usability_reasons"] else account["status"]
        print(
            f"  {account['account_id']} [{status_label}/{account['usable']}]: "
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
            print(
                f"ALERT Anthropic cache-read ratio (last hour): {cache['ratio_percent']:.1f}% "
                f"(<{CACHE_ALERT_READ_PERCENT}%): sessions are rewriting their context; check the proxy"
            )
        elif cache["state"] == "ok":
            print(f"Anthropic cache-read ratio (last hour): {cache['ratio_percent']:.1f}%")
        else:
            print("Anthropic cache-read ratio (last hour): unknown")
    if http_status := payload.get("http_status"):
        for name, counts in sorted(http_status["providers"].items()):
            print(
                f"{name} HTTP last {http_status['window_minutes']}m: {counts['requests']} requests, "
                f"{counts['http_4xx']} 4xx, {counts['http_5xx']} 5xx, {counts['failed_without_status']} failed without status"
            )
    for name, pool in (payload.get("db_pool") or {}).items():
        if pool.get("kind") == "queue":
            print(
                f"db pool {name}: {pool['checked_out']}/{pool['size']} checked out, overflow {pool['overflow']}/{pool['max_overflow']}"
            )
    print(payload["availability_note"])


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _format_reset_credits(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _format_window(window: dict[str, Any] | None) -> str:
    if not window or window["remaining_percent"] is None:
        return "unknown"
    remaining = window["remaining_percent"]
    display = int(remaining) if remaining.is_integer() else remaining
    reset = f" reset {window['reset_at']}" if window["reset_at"] else ""
    return f"{display}%{reset}"
