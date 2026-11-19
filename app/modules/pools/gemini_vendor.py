from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

HEADLINE_PREFIXES = ("gemini-4-neon",)
HEADLINE_IDS = frozenset({"gemini-pro-orchestrator-neon"})
SESSION_SPAN = timedelta(hours=5, minutes=5)
SESSION_MINUTES = 300
WEEKLY_MINUTES = 10080
FAMILY_HEADLINE = "Gemini"
FAMILY_OTHER_GEMINI = "Gemini (other models)"
FAMILY_THIRD_PARTY = "Claude and GPT (Antigravity)"
OTHER_GEMINI_BUCKET = "gemini-models"
SUMMARY_NEON_PREFIX = "gemini-neon-"
SUMMARY_SKIPPED_PREFIXES = ("gemini-argon-", "gemini-carbon-")


def is_headline(model: str) -> bool:
    return model in HEADLINE_IDS or model.startswith(HEADLINE_PREFIXES)


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    head, dot, rest = text.partition(".")
    if dot:
        digits = len(rest) - len(rest.lstrip("0123456789"))
        text = f"{head}.{rest[:digits][:6]}{rest[digits:]}"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _window(
    window_id: str, family: str, kind: str, label: str, quota: Any, minutes: int | None
) -> dict[str, Any] | None:
    if not isinstance(quota, dict):
        return None
    fraction = quota.get("remainingFraction")
    if not isinstance(fraction, (int, float)) or isinstance(fraction, bool):
        return None
    fraction = max(0.0, min(1.0, float(fraction)))
    return {
        "id": window_id,
        "family": family,
        "kind": kind,
        "label": label,
        "usedPercent": round((1.0 - fraction) * 100.0, 3),
        "resetsAt": None if fraction >= 1.0 else iso(parse_time(quota.get("resetTime"))),
        "windowDurationMins": minutes,
    }


def _quota_kind(quota: Any, now: datetime) -> str:
    if not isinstance(quota, dict):
        return "other"
    fraction = quota.get("remainingFraction")
    if isinstance(fraction, (int, float)) and fraction >= 1.0:
        return "session"
    resets = parse_time(quota.get("resetTime"))
    if resets is not None and timedelta(0) <= resets - now <= SESSION_SPAN:
        return "session"
    return "other"


def _keep_most_used(target: dict[str, dict[str, Any]], window: dict[str, Any] | None) -> None:
    if window is None:
        return
    previous = target.get(window["id"])
    if previous is None or window["usedPercent"] > previous["usedPercent"]:
        target[window["id"]] = window


def _most_used_by_kind(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    for window in windows:
        previous = chosen.get(window["kind"])
        if previous is None or window["usedPercent"] > previous["usedPercent"]:
            chosen[window["kind"]] = window
    return [chosen[kind] for kind in ("session", "other", "weekly") if kind in chosen]


def model_windows(answer: Any, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    models = answer.get("models") if isinstance(answer, dict) else None
    if not isinstance(models, dict):
        raise ValueError("models")
    headline: list[dict[str, Any]] = []
    others: dict[str, dict[str, Any]] = {}
    for model_id, model in models.items():
        if not isinstance(model_id, str) or not isinstance(model, dict) or not model_id.startswith("gemini-"):
            continue
        short = model.get("quotaInfo")
        kind = _quota_kind(short, now)
        short_label = "5-hour" if kind == "session" else "Reported quota"
        short_minutes = SESSION_MINUTES if kind == "session" else None
        prefix, family = (
            (model_id, FAMILY_HEADLINE) if is_headline(model_id) else (OTHER_GEMINI_BUCKET, FAMILY_OTHER_GEMINI)
        )
        pair = (
            _window(f"{prefix}:quota", family, kind, f"{family} · {short_label}", short, short_minutes),
            _window(
                f"{prefix}:weekly", family, "weekly", f"{family} · Weekly", model.get("weeklyQuotaInfo"), WEEKLY_MINUTES
            ),
        )
        for window in pair:
            if window is None:
                continue
            if family == FAMILY_HEADLINE:
                headline.append(window)
            else:
                _keep_most_used(others, window)
    return _most_used_by_kind(headline), list(others.values())


def _summary_family(bucket_id: str, group_name: Any) -> str:
    if bucket_id.startswith("3p-"):
        return FAMILY_THIRD_PARTY
    if bucket_id.startswith("gemini-"):
        return FAMILY_HEADLINE
    return group_name.strip() if isinstance(group_name, str) and group_name.strip() else bucket_id


def summary_windows(answer: Any, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(answer, dict):
        raise ValueError("summary")
    groups = answer.get("groups") if isinstance(answer.get("groups"), list) else []
    if not any(isinstance(group, dict) and group.get("buckets") for group in groups):
        groups = [{"buckets": answer.get("buckets") or []}]
    windows: dict[str, dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("buckets"), list):
            continue
        for bucket in group["buckets"]:
            if not isinstance(bucket, dict) or bucket.get("disabled") is True:
                continue
            bucket_id = bucket.get("bucketId")
            if not isinstance(bucket_id, str) or not bucket_id:
                continue
            span = bucket.get("window")
            kind = "weekly" if span == "weekly" else "session" if span == "5h" else "other"
            label = {"weekly": "Weekly", "session": "5-hour"}.get(kind, "Quota")
            minutes = {"weekly": WEEKLY_MINUTES, "session": SESSION_MINUTES}.get(kind)
            family = _summary_family(bucket_id, group.get("displayName"))
            window = _window(bucket_id, family, kind, f"{family} · {label}", bucket, minutes)
            if window is not None:
                windows[bucket_id] = window
    neon = [w for w in windows.values() if w["id"].startswith(SUMMARY_NEON_PREFIX)]
    plain = [
        w
        for w in windows.values()
        if w["id"].startswith("gemini-") and not w["id"].startswith((SUMMARY_NEON_PREFIX, *SUMMARY_SKIPPED_PREFIXES))
    ]
    chosen = neon or plain
    chosen_ids = {w["id"] for w in chosen}
    return _most_used_by_kind(chosen), [w for w in windows.values() if w["id"] not in chosen_ids]
