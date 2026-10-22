from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from clients.seat_constants import EXIT_FAILED, EXIT_NO_ACCOUNT, EXIT_OK
from clients.seat_probe import apply_probe, run_probe
from clients.seat_selection import active_leases, cooling, usage_since
from clients.seat_state import (
    fail,
    iso,
    lease_accounts,
    load_registry,
    load_state,
    now,
    parse_ts,
    registry_path,
    seat_home,
    state_path,
    update_state,
    write_json,
)


def command_accounts(args: argparse.Namespace) -> int:
    registry = load_registry() + lease_accounts()
    state = load_state()
    day_ago = now() - timedelta(hours=24)
    rows = []
    for account in registry:
        record = state["accounts"].get(account["id"], {})
        until = cooling(record)
        leases = active_leases(state, account["id"], now())
        rows.append(
            {
                "id": account["id"],
                "vendor": account.get("vendor"),
                "plan": account.get("plan"),
                "auth": account.get("auth"),
                "enabled": account.get("enabled", True) is not False,
                "auth_ok": record.get("auth_ok"),
                "auth_checked_at": record.get("auth_checked_at"),
                "identity": record.get("identity") or {},
                "cooldown_until": iso(until) if until else None,
                "cooldowns": {
                    pool: value
                    for pool, value in (record.get("cooldowns") or {}).items()
                    if (stamp := parse_ts(value)) and stamp > now()
                },
                "model_unavailable": {
                    model: value
                    for model, value in (record.get("model_unavailable") or {}).items()
                    if (stamp := parse_ts(value)) and stamp > now()
                },
                "last_error": record.get("last_error"),
                "last_run_at": record.get("last_run_at"),
                "last_24h": usage_since(record, day_ago),
                "leases": {
                    "active": len(leases),
                    "next_expiry": min((lease["expires_at"] for lease in leases), default=None),
                },
            }
        )
        if account.get("expires_at"):
            rows[-1]["expires_at"] = account["expires_at"]
    if args.json:
        print(json.dumps({"state": str(state_path()), "accounts": rows}, indent=2, sort_keys=True))
        return EXIT_OK
    print(
        f"{'account':<22} {'vendor':<7} {'auth':<8} {'status':<22} "
        f"{'24h runs':>8} {'tok in':>9} {'tok out':>8} {'leases':>6}"
    )
    for row in rows:
        if not row["enabled"]:
            status = "disabled"
        elif row["cooldown_until"]:
            status = f"cooling to {row['cooldown_until'][11:16]}Z"
        elif row["cooldowns"]:
            labels = {"cursor-models": "Composer", "cursor-other": "Other models"}
            status = "; ".join(
                f"{labels.get(pool, pool)} out to {value[5:10]}" for pool, value in row["cooldowns"].items()
            )
        elif row["auth_ok"] is False:
            status = "re-login needed"
        elif row["auth_ok"] is None:
            status = "not probed"
        else:
            status = "ok " + (row["identity"].get("tier") or "")
        usage = row["last_24h"]
        print(
            f"{row['id'] + (' [' + row['plan'] + ']' if row['plan'] else ''):<22} "
            f"{row['vendor']:<7} {row['auth']:<8} {status:<22} "
            f"{usage['runs']:>8} {usage['tokens_in']:>9} {usage['tokens_out']:>8} {row['leases']['active']:>6}"
        )
        for model, until in row["model_unavailable"].items():
            print(f"  model unavailable: {model} until {until}")
        if row.get("expires_at"):
            print(f"  expires {row['expires_at']}")
    return EXIT_OK


def command_add(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", args.id):
        fail("account id must be lowercase letters, digits and dashes")
    account: dict[str, Any] = {"id": args.id, "vendor": args.vendor, "enabled": True, "added_at": iso(now())}
    if args.vendor == "cursor":
        if args.api_key_prompt:
            key = getpass.getpass(f"Cursor user API key for {args.id} (input hidden): ").strip()
            if not key:
                fail("no key entered")
            key_file = seat_home() / "keys" / f"{args.id}.key"
            account.update({"auth": "api-key", "key_file": str(key_file)})
        elif args.api_key_file:
            key_file = Path(args.api_key_file).expanduser()
            if not key_file.is_file():
                fail(f"{key_file} does not exist")
            if key_file.stat().st_mode & 0o077:
                fail(f"{key_file} is readable by others; chmod 600 it first")
            account.update({"auth": "api-key", "key_file": str(key_file)})
        else:
            account["auth"] = "login"
    elif args.vendor == "muse":
        if args.api_key_file or args.api_key_prompt or args.data_dir:
            fail("Muse uses existing Studio login; no credential files are accepted")
        if sys.platform != "darwin":
            fail("Muse login seats are Studio-local; box credentials are not provisioned")
        account["auth"] = "login"
    else:
        account["auth"] = "data-dir"
        if args.data_dir:
            account["data_dir"] = str(Path(args.data_dir).expanduser())

    def add(registry: list[dict[str, Any]], state: dict[str, Any]) -> None:
        if any(a["id"] == args.id for a in registry):
            fail(f"account {args.id} already registered")
        if args.vendor == "muse" and any(a.get("vendor") == "muse" for a in registry):
            fail("one Muse login account per host; credentials are never copied or leased")
        if args.vendor == "cursor" and account["auth"] == "login" and any(a.get("vendor") == "cursor" and a.get("auth") == "login" for a in registry):
            fail("a Cursor login account exists already; extra Cursor accounts need --api-key-prompt")
        if args.vendor == "devin" and not account.get("data_dir") and any(
                a.get("vendor") == "devin" and not a.get("data_dir") for a in registry):
            fail("a Devin account on the default data dir exists already; pass --data-dir for this one")
        if args.vendor == "cursor" and args.api_key_prompt:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(key + "\n")
        registry.append(account)
        write_json(registry_path(), {"version": 1, "accounts": registry})

    update_state(add)
    probe = run_probe(account)[0]
    apply_probe(args.id, probe)
    print(f"registered {args.id} ({args.vendor}, {account['auth']}): auth {'ok' if probe['auth_ok'] else 'FAILED'}")
    return EXIT_OK if probe["auth_ok"] else EXIT_FAILED


def command_set(args: argparse.Namespace) -> int:
    def apply(registry: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
        account = next((entry for entry in registry if entry["id"] == args.id), None)
        if account is None:
            fail(f"no account {args.id} registered", EXIT_NO_ACCOUNT)
        if args.plan is not None:
            account["plan"] = args.plan or None
        if args.tier is not None:
            account["tier"] = args.tier
        if args.cycle_day is not None:
            account["cycle_day"] = args.cycle_day
        write_json(registry_path(), {"version": 1, "accounts": registry})
        return {"id": args.id, "tier": account.get("tier"), "cycle_day": account.get("cycle_day")}

    result = update_state(apply)
    print(json.dumps(result, sort_keys=True) if args.json else f"updated {args.id}")
    return EXIT_OK
