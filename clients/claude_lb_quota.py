from __future__ import annotations  # noqa: I001


import json
import os
import sys
import time
from datetime import datetime

from clients.claude_lb_config import FABLE_SCOPED_WEEKLY_QUOTA_KEY
from clients.claude_lb_config import WAIT_BUFFER_SECONDS


def short_account_name(account: dict) -> str:
    return (
        account.get("alias")
        or account.get("displayName")
        or account.get("email")
        or str(account.get("accountId", "unknown"))[:8]
    )


def number(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def reset_label(reset_at: object) -> str:
    reset = number(reset_at, 0.0)
    if reset <= 0:
        return "reset unknown"
    remaining = max(0, int(reset - time.time()))
    if remaining < 90:
        return "resets <2m"
    if remaining < 3600:
        return f"resets {round(remaining / 60)}m"
    return f"resets {round(remaining / 3600)}h"


def stderr_color() -> bool:
    return sys.stderr.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if stderr_color() else text


def pct_left(remaining: float) -> str:
    label = f"{remaining:.0f}%"
    if remaining >= 50:
        return c(label, "32")
    if remaining >= 20:
        return c(label, "33")
    return c(label, "31")


def fable_remaining_percent(account: dict) -> float | None:
    for quota in account.get("additionalQuotas") or []:
        if quota.get("quotaKey") != FABLE_SCOPED_WEEKLY_QUOTA_KEY:
            continue
        window = quota.get("primaryWindow")
        if not isinstance(window, dict) or window.get("usedPercent") is None:
            return None
        used_percent = number(window.get("usedPercent"), -1.0)
        if used_percent < 0:
            return None
        return max(0.0, min(100.0, 100.0 - used_percent))
    return None


def fable_availability_label(account: dict) -> str:
    eligible = account.get("fableEligible")
    remaining = fable_remaining_percent(account)
    if remaining is not None:
        remaining_label = pct_left(remaining)
        if eligible is False:
            return f"fable out ({remaining_label} left)"
        return f"fable {remaining_label} left"
    if eligible is True:
        return f"fable {c('available', '32')}"
    if eligible is False:
        return f"fable {c('out', '31')}"
    return ""


def quota_window(account: dict, quota_key: str) -> dict:
    for quota in account.get("additionalQuotas") or []:
        if quota.get("quotaKey") == quota_key:
            return quota.get("primaryWindow") or {}
    return {}


def account_pressure(account: dict, quota_key: str) -> tuple[bool, float, float, float, str, str]:
    window = quota_window(account, quota_key)
    quota_used_percent = number(window.get("usedPercent"), 0.0)
    reset_at = number(window.get("resetAt"), 0.0)
    blocked = quota_used_percent >= 100.0 and reset_at > time.time()

    usage = account.get("usage") or {}
    primary_remaining = number(
        usage.get("primaryRemainingPercent", account.get("remainingCreditsPrimary")),
        100.0,
    )
    weekly_remaining = number(
        usage.get("secondaryRemainingPercent", account.get("remainingCreditsSecondary")),
        100.0,
    )
    primary_used = max(0.0, min(100.0, 100.0 - primary_remaining))
    weekly_used = max(0.0, min(100.0, 100.0 - weekly_remaining))
    quota_name = quota_key.replace("anthropic_", "").replace("_", "-")
    quota_left = max(0.0, min(100.0, 100.0 - quota_used_percent))
    parts = [
        f"left: {quota_name} {pct_left(quota_left)}",
        f"5h {pct_left(100.0 - primary_used)}",
        f"weekly {pct_left(100.0 - weekly_used)}",
    ]
    fable_label = fable_availability_label(account)
    if fable_label:
        parts.append(fable_label)
    reason = " · ".join(parts)
    if blocked:
        reason = f"{reason} · {c(reset_label(reset_at), '31')}"
    return blocked, primary_used, weekly_used, quota_used_percent, short_account_name(account), reason


def error_message_from_body(body: str) -> str:
    text = body.strip()
    if not text:
        return "Request failed"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return text


def prettify_anthropic_selection_error(message: str) -> str:
    marker = "Anthropic account"
    status_marker = "; statuses: "
    if marker not in message or "none are selectable" not in message or status_marker not in message:
        return message

    count_text = message.split(marker, 1)[0].strip().split()[-1:]
    count = count_text[0] if count_text else ""
    status_summary = message.split(status_marker, 1)[1].split(".", 1)[0].strip()
    if not count or not status_summary:
        return message

    if status_summary == f"rate_limited={count}":
        summary = f"all Claude accounts are rate-limited ({count} accounts)"
    else:
        summary = f"Claude accounts are all at limit or unavailable ({count} accounts; {status_summary})"

    quota_detail = ""
    quota_marker = "Model quota: "
    if quota_marker in message:
        raw_quota_detail = message.split(quota_marker, 1)[1]
        raw_quota_detail = raw_quota_detail.split(" OpenAI accounts", 1)[0]
        raw_quota_detail = raw_quota_detail.split(" Limits reset", 1)[0].strip()
        if raw_quota_detail.endswith("."):
            raw_quota_detail = raw_quota_detail[:-1]
        if raw_quota_detail:
            quota_detail = f" Model quota: {raw_quota_detail}."

    suffix = ""
    if "OpenAI accounts are not eligible for Claude routing" in message:
        suffix = " OpenAI accounts cannot serve Claude."
    return f"{summary}.{quota_detail}{suffix}"


def format_lb_pick_error(body: str) -> str:
    return prettify_anthropic_selection_error(error_message_from_body(body))


def epoch_from_iso(value: str) -> int | None:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def retry_at_from_error_body(body: str) -> int | None:

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None
    retry_at = error.get("retryAt")
    if isinstance(retry_at, str):
        epoch = epoch_from_iso(retry_at)
        if epoch is not None:
            return epoch
    retry_after = error.get("retryAfterSeconds")
    if isinstance(retry_after, (int, float)) and retry_after >= 0:
        return int(time.time() + retry_after)
    return None


def wait_mode() -> str:
    value = (os.environ.get("CLAUDE_LB_WAIT_FOR_LIMIT") or "auto").strip().lower()
    return value if value in {"auto", "always", "never"} else "auto"


def wait_max_seconds() -> int:
    try:
        return max(0, int(os.environ.get("CLAUDE_LB_WAIT_MAX_SECONDS", "21600")))
    except ValueError:
        return 21600


def should_wait_for_reset(retry_at: int | None, deadline: float) -> bool:
    if retry_at is None or wait_mode() == "never":
        return False
    return retry_at + WAIT_BUFFER_SECONDS <= deadline


def wait_until_reset(retry_at: int, reason: str) -> None:
    target = retry_at + WAIT_BUFFER_SECONDS
    local_label = time.strftime("%H:%M:%S", time.localtime(target))
    print(
        f"cc: {reason} Waiting until {c(local_label, '1;33')} for limits to reset (Ctrl-C to abort).",
        file=sys.stderr,
    )
    while True:
        remaining = target - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30.0))
