from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from seat_environment import (  # pyrefly: ignore[missing-import]
    devin_data_dir,
    read_cursor_key,
)
from seat_storage import (  # pyrefly: ignore[missing-import]
    EXIT_OK,
    iso,
    load_registry,
    now,
    parse_ts,
    update_state,
)


class QuotaError(Exception):
  pass


def quota_window(
  window_id: str, kind: str, label: str, used: Any, reset: Any, duration: int | None = None, source: str = "vendor"
) -> dict[str, Any]:
  percent = float(used)
  if not math.isfinite(percent):
    raise ValueError("invalid quota percent")
  if isinstance(reset, (int, float)) or (isinstance(reset, str) and reset.isdigit()):
    seconds = float(reset)
    reset = iso(datetime.fromtimestamp(seconds / 1000 if seconds > 1e11 else seconds, timezone.utc))
  else:
    reset = iso(stamp) if (stamp := parse_ts(reset)) else None
  return {
    "id": window_id,
    "kind": kind,
    "label": label,
    "durationMins": duration,
    "usedPercent": percent,
    "remainingPercent": max(0, min(100, 100 - percent)),
    "resetsAt": reset,
    "source": source,
  }


def quota_payload(windows: list[dict[str, Any]], source: str = "vendor", observed: str | None = None) -> dict[str, Any]:
  available = [window for window in windows if window["usedPercent"] < 100]
  primary = next((window for window in windows if window["id"] == "cursor-models"), None)
  if primary is None:
    primary = max(available or windows, key=lambda item: item["usedPercent"], default=None)
  return {
    "source": source,
    "observedAt": observed or iso(now()),
    "stale": False,
    "unavailableReason": None,
    "primaryWindowId": primary["id"] if primary else None,
    "windows": windows,
  }


def cursor_quota(usage: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
  values = usage.get("planUsage") or {}
  info = plan.get("planInfo") or plan
  reset = usage.get("billingCycleEnd") or values.get("billingCycleEnd")
  windows = [
    quota_window(window_id, "monthly", label, values[field], reset)
    for window_id, label, field in (
      ("cursor-models", "Composer", "autoPercentUsed"),
      ("cursor-other", "Other models", "apiPercentUsed"),
    )
    if values.get(field) is not None
  ]
  if not windows:
    raise QuotaError("vendor_error")
  stamp = parse_ts(windows[0]["resetsAt"])
  for window in windows:
    if stamp and stamp - now() <= timedelta(days=8):
      window["kind"] = "weekly"
  reading = quota_payload(windows)
  reading["planLabel"] = info.get("planName") or info.get("planType")
  reading["email"] = plan.get("email")
  return reading


def devin_quota(response: dict[str, Any]) -> dict[str, Any]:
  status = response.get("userStatus") or response
  plan = status.get("planStatus") or status.get("planInfo") or status
  windows = []
  for window_id, minutes in (("daily", 1440), ("weekly", 10080)):
    if window_id == "daily" and (status.get("hideDailyQuota") or plan.get("hideDailyQuota")):
      continue
    remaining = status.get(f"{window_id}QuotaRemainingPercent", plan.get(f"{window_id}QuotaRemainingPercent"))
    reset = (
      status.get(f"{window_id}QuotaResetAtUnix")
      or plan.get(f"{window_id}QuotaResetAtUnix")
      or status.get(f"{window_id}ResetAtUnix")
      or plan.get(f"{window_id}ResetAtUnix")
    )
    if remaining is not None:
      windows.append(
        quota_window(window_id, window_id, window_id.title(), 100 - float(remaining), reset, minutes)
      )
  if not windows:
    raise QuotaError("vendor_error")
  reading = quota_payload(windows)
  info = plan.get("planInfo") or plan
  reading["planLabel"] = info.get("planName") or info.get("name")
  reading["email"] = status.get("email")
  return reading


def muse_quota(text: str) -> dict[str, Any] | None:
  latest = None

  def visit(value: Any) -> None:
    nonlocal latest
    if not isinstance(value, dict):
      return
    window = value.get("window")
    weekly = value.get("weekly") or value.get("weeklyUsage")
    if isinstance(window, dict) or isinstance(weekly, dict):
      windows = []
      for window_id, item in (("session", window), ("weekly", weekly)):
        if not isinstance(item, dict):
          continue
        used = item.get("usedPercent", item.get("percentUsed", item.get("utilization")))
        if used is None:
          continue
        windows.append(
          quota_window(
            window_id,
            window_id,
            window_id.title(),
            used,
            item.get("resetsAt", item.get("resetAt")),
            item.get("windowDurationMins"),
            "vendor_observed",
          )
        )
      if windows:
        observed = value.get("observedAtMs")
        latest = quota_payload(
          windows,
          "vendor_observed",
          iso(datetime.fromtimestamp(float(observed) / 1000, timezone.utc)) if observed else None,
        )
    for child in value.values():
      if isinstance(child, dict):
        visit(child)

  for line in text.splitlines():
    try:
      visit(json.loads(line))
    except (ValueError, TypeError, OverflowError):
      continue
  return latest


def cursor_access_credential(account: dict[str, Any]) -> str:
  if account.get("auth") == "api-key":
    return read_cursor_key(account)
  if sys.platform != "darwin":
    raise QuotaError("credential_unavailable")
  try:
    result = subprocess.run(
      ["/usr/bin/security", "find-generic-password", "-s", "cursor-access-token", "-a", "cursor-user", "-w"],
      capture_output=True,
      text=True,
      timeout=10,
    )
  except (OSError, subprocess.SubprocessError):
    raise QuotaError("credential_unavailable") from None
  if result.returncode or not result.stdout.strip():  # pyrefly: ignore[unbound-name]
    raise QuotaError("credential_unavailable")
  return result.stdout.strip()


def quota_post(url: str, body: dict[str, Any], bearer: str | None = None) -> dict[str, Any]:
  headers = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
  if bearer:
    headers.update({"Authorization": f"Bearer {bearer}", "x-cursor-client-type": "cli"})
  request = Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
  with urlopen(request, timeout=15) as response:
    value = json.load(response)
  if not isinstance(value, dict):
    raise QuotaError("vendor_error")
  return value


def collect_quota(account: dict[str, Any], observed: dict[str, Any] | None = None) -> dict[str, Any]:
  vendor = account["vendor"]
  if observed:
    return observed
  if vendor == "muse":
    raise QuotaError("not_reported")
  if vendor == "cursor":
    key = cursor_access_credential(account)
    base = "https://api2.cursor.sh/aiserver.v1.DashboardService/"
    try:
      usage = quota_post(base + "GetCurrentPeriodUsage", {}, key)
      plan = quota_post(base + "GetPlanInfo", {}, key)
    except HTTPError as error:
      if account.get("auth") == "api-key" and error.code in (401, 403):
        raise QuotaError("auth_unsupported") from None
      raise QuotaError("vendor_error") from None
    return cursor_quota(usage, plan)
  try:
    credentials = tomllib.loads((devin_data_dir(account) / "devin" / "credentials.toml").read_text())
    key = credentials["windsurf_api_key"]
  except (OSError, ValueError, KeyError):
    raise QuotaError("credential_unavailable") from None
  base = credentials.get("api_server_url") or "https://server.codeium.com"
  response = quota_post(
    base.rstrip("/") + "/exa.seat_management_pb.SeatManagementService/GetUserStatus",
    {
      "metadata": {
        "apiKey": key,
        "ideName": "agent-lb",
        "ideVersion": "1.0.0",
        "extensionName": "agent-lb",
        "extensionVersion": "1.0.0",
        "locale": "en",
      }
    },
  )
  return devin_quota(response)


def refresh_quota(account: dict[str, Any], observed: dict[str, Any] | None = None) -> dict[str, Any]:
  try:
    reading = collect_quota(account, observed)
    reason = None
  except QuotaError as error:
    reading, reason = None, str(error)
  except (ValueError, TypeError):
    reading, reason = None, "vendor_parse"
  except (OSError, RuntimeError, KeyError, subprocess.SubprocessError):
    reading, reason = None, "vendor_error"

  def apply(registry: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    record = state["accounts"].setdefault(account["id"], {})
    previous = record.get("quota") or {}
    if reading is not None:
      value = dict(reading)
      for key, field in (("email", "email"), ("planLabel", "plan_label")):
        label = value.pop(key, None)
        if isinstance(label, str) and label:
          record[field] = label
      record["quota"] = value
      seen, limits = parse_ts(value.get("observedAt")), record.get("limits") or {}
      for window in value["windows"]:
        at = parse_ts(limits.get(window["id"], {}).get("at"))
        if window["usedPercent"] < 100 and seen and at and seen > at:
          for key in ("cooldowns", "limits"):
            (record.get(key) or {}).pop(window["id"], None)
    elif previous.get("windows"):
      stamp = parse_ts(previous.get("observedAt"))
      record["quota"] = {
        **previous,
        "stale": stamp is None or now() - stamp > timedelta(minutes=15),
        "unavailableReason": reason,
      }
    else:
      record["quota"] = {
        "source": "unavailable",
        "observedAt": None,
        "stale": False,
        "unavailableReason": reason,
        "primaryWindowId": None,
        "windows": [],
      }
    state["quota_refreshed_at"] = iso(now())
    return record["quota"]

  return update_state(apply)


def command_quota(args: argparse.Namespace) -> int:
  accounts = [
    account
    for account in load_registry()
    if (not args.vendor or account["vendor"] == args.vendor) and (not args.account or account["id"] == args.account)
  ]
  result = {}
  plans = {}
  for account in accounts:
    plan = account.get("plan") if account.get("vendor") == "cursor" else None
    result[account["id"]] = refresh_quota(account, plans.get(plan))
    if plan and result[account["id"]].get("source") == "vendor":
      plans[plan] = result[account["id"]]
  print(
    json.dumps({"accounts": result})
    if args.json
    else "\n".join(f"{account_id}: {quota['source']}" for account_id, quota in result.items())
  )
  return EXIT_OK
