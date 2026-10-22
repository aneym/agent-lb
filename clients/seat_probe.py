from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from typing import Any

from seat_environment import (  # pyrefly: ignore[missing-import]
    account_env,
)
from seat_storage import (  # pyrefly: ignore[missing-import]
    AUTH_RE,
    ERROR_CHARS,
    EXIT_FAILED,
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    PROBE_TIMEOUT_SECONDS,
    cli_bin,
    fail,
    iso,
    load_registry,
    muse_version_ok,
    now,
    update_state,
)


def run_probe(account: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
  vendor = account.get("vendor")
  if vendor == "muse":
    command = [cli_bin("muse"), "--version"]
  elif vendor == "devin":
    command = [cli_bin("devin"), "auth", "status"]
  elif account.get("auth") == "api-key":
    command = [cli_bin("cursor"), "models"]
  else:
    command = [cli_bin("cursor"), "about"]
  started = time.monotonic()
  try:
    completed = subprocess.run(
      command,
      env=account_env(account),
      stdin=subprocess.DEVNULL,
      capture_output=True,
      text=True,
      timeout=PROBE_TIMEOUT_SECONDS,
      check=False,
    )
  except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
    return {"auth_ok": False, "auth_error": f"{type(error).__name__}: {error}"[:ERROR_CHARS]}, None
  text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", completed.stdout + "\n" + completed.stderr)
  if vendor == "muse":
    ok = completed.returncode == 0 and muse_version_ok(text)
    identity = {"version": text.strip(), "auth_source": "existing-login-unverified"}
  elif vendor == "cursor" and account.get("auth") == "api-key":
    ok = completed.returncode == 0 and not AUTH_RE.search(text)
    identity = {}
  elif vendor == "cursor":
    email = re.search(r"^User Email\s+(\S+@\S+)\s*$", text, re.M)
    tier = re.search(r"^Subscription Tier\s+(.+?)\s*$", text, re.M)
    ok = completed.returncode == 0 and email is not None
    identity = {"email": email.group(1)} if email else {}
    if tier:
      identity["tier"] = tier.group(1)
  else:
    ok = completed.returncode == 0 and re.search(r"^\s*Logged in", text, re.M) is not None
    identity = {}
    for field, label in (("email", "Email"), ("tier", "Tier"), ("plan", "Plan")):
      found = re.search(rf"^\s*{label}:\s*(.+?)\s*$", text, re.M)
      if found:
        identity[field] = found.group(1)
  result: dict[str, Any] = {
    "auth_ok": ok,
    "auth_checked_at": iso(now()),
    "probe_ms": round((time.monotonic() - started) * 1000),
    "identity": identity,
    "auth_error": None if ok else (text.strip().splitlines() or ["no output"])[0][:ERROR_CHARS],
  }
  return result, text


def apply_probe(account_id: str, probe: dict[str, Any]) -> None:
  def apply(registry: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if any(account["id"] == account_id for account in registry):
      record = state["accounts"].setdefault(account_id, {})
      record.update(probe)
      record.pop("model_unavailable", None)
      record.pop("model_unavailable_text", None)

  update_state(apply)


def command_probe(args: argparse.Namespace) -> int:
  registry = load_registry()
  if args.account and not any(account["id"] == args.account for account in registry):
    fail(f"no account {args.account!r} registered", EXIT_NO_ACCOUNT)
  results = {}
  for account in registry:
    if (
      args.account
      and account["id"] != args.account
      or args.vendor
      and account.get("vendor") != args.vendor
      or account.get("enabled", True) is False
    ):
      continue
    probe = run_probe(account)[0]
    apply_probe(account["id"], probe)
    results[account["id"]] = probe
  if args.json:
    print(json.dumps(results, indent=2, sort_keys=True))
  else:
    for account_id, probe in results.items():
      verdict = "ok" if probe["auth_ok"] else f"FAILED ({probe.get('auth_error')})"
      print(f"{account_id:<24} {verdict}")
  return EXIT_OK if all(probe["auth_ok"] for probe in results.values()) else EXIT_FAILED
