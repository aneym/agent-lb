from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.core.clients.code_assist import CodeAssistError, post_code_assist
from app.core.clients.usage import UsageFetchError
from app.core.config.settings import get_settings
from app.core.usage.models import RateLimitPayload, UsagePayload, UsageWindow
from app.modules.pools import gemini_vendor
from app.modules.proxy.gemini_code_assist_errors import upstream_error

SUMMARY_PATH = "/v1internal:retrieveUserQuotaSummary"
MODELS_PATH = "/v1internal:fetchAvailableModels"

type _WindowParser = Callable[[Any, datetime], tuple[list[dict[str, Any]], list[dict[str, Any]]]]
_QUOTA_READS: tuple[tuple[str, _WindowParser], ...] = (
    (SUMMARY_PATH, gemini_vendor.summary_windows),
    (MODELS_PATH, gemini_vendor.model_windows),
)
_ACCOUNT_LIMIT_KINDS = frozenset({"rate_limit", "quota"})


def _usage_error(exc: CodeAssistError) -> UsageFetchError:
    if exc.status_code != 429:
        return UsageFetchError(exc.status_code, exc.message)
    failure = upstream_error(exc.status_code, exc.body)
    if failure.reset_at is None:
        return UsageFetchError(exc.status_code, exc.message)
    retry_after = max(0.0, failure.reset_at - time.time())
    reset_at = math.ceil(failure.reset_at) if failure.kind in _ACCOUNT_LIMIT_KINDS else None
    return UsageFetchError(exc.status_code, exc.message, retry_after_seconds=retry_after, reset_at=reset_at)


def _to_usage_window(window: Mapping[str, Any], *, default_minutes: int) -> UsageWindow | None:
    used = window.get("usedPercent")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    minutes_raw = window.get("windowDurationMins")
    minutes = int(minutes_raw) if isinstance(minutes_raw, int) and minutes_raw > 0 else default_minutes
    resets = gemini_vendor.parse_time(window.get("resetsAt"))
    reset_at = int(resets.timestamp()) if resets is not None else None
    return UsageWindow(
        used_percent=max(0.0, min(100.0, float(used))),
        reset_at=reset_at,
        limit_window_seconds=minutes * 60,
    )


def _extract_windows(headline: list[dict[str, Any]]) -> tuple[UsageWindow | None, UsageWindow | None]:
    primary: UsageWindow | None = None
    secondary: UsageWindow | None = None
    for item in headline:
        kind = item.get("kind")
        if kind == "session" and primary is None:
            primary = _to_usage_window(item, default_minutes=gemini_vendor.SESSION_MINUTES)
        elif kind == "weekly" and secondary is None:
            secondary = _to_usage_window(item, default_minutes=gemini_vendor.WEEKLY_MINUTES)
    return primary, secondary


async def fetch_gemini_usage(*, access_token: str, project_id: str | None = None) -> UsagePayload:
    timeout = get_settings().usage_fetch_timeout_seconds
    cleaned_project = project_id.strip() if isinstance(project_id, str) else ""
    body: dict[str, object] = {"project": cleaned_project} if cleaned_project else {}
    now = datetime.now(timezone.utc)
    primary: UsageWindow | None = None
    secondary: UsageWindow | None = None
    last_error: CodeAssistError | None = None

    for path, parse in _QUOTA_READS:
        if primary is not None and secondary is not None:
            break
        try:
            answer = await post_code_assist(path, access_token, body, timeout_seconds=timeout)
        except CodeAssistError as exc:
            error = _usage_error(exc)
            no_window = primary is None and secondary is None
            if exc.status_code == 401 or error.reset_at is not None or (exc.status_code == 429 and no_window):
                raise error from exc
            last_error = exc
            continue
        try:
            headline, _ = parse(answer, now)
        except ValueError:
            continue
        found_primary, found_secondary = _extract_windows(headline)
        if primary is None and found_primary is not None:
            primary = found_primary
        if secondary is None and found_secondary is not None:
            secondary = found_secondary

    if primary is None and secondary is None and last_error is not None:
        raise _usage_error(last_error) from last_error

    return UsagePayload(
        rate_limit=RateLimitPayload(
            primary_window=primary,
            secondary_window=secondary,
        )
    )
