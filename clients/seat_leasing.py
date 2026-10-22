from __future__ import annotations

import argparse
import json
import shlex
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from clients.seat_constants import EXIT_FAILED, EXIT_NO_ACCOUNT, EXIT_OK, SEAT_FOR_VENDOR
from clients.seat_credentials import devin_data_dir, read_cursor_key
from clients.seat_selection import cooling, order_accounts
from clients.seat_state import iso, now, private_dir, private_file, update_state
from clients.seat_usage import append_ledger


def lease_error(
    code: str, vendor: str, message: str, json_output: bool, skipped: list[dict[str, str]] | None = None
) -> Any:
    print(f"seat: {message}", file=sys.stderr)
    if json_output:
        result: dict[str, Any] = {"error": code, "vendor": vendor, "message": message}
        if skipped is not None:
            result["skipped"] = skipped
        print(json.dumps(result, sort_keys=True))
    raise SystemExit(EXIT_NO_ACCOUNT if code in ("no_account", "unknown_account", "not_leasable") else EXIT_FAILED)


def leasable(account: dict[str, Any]) -> bool:
    return (account.get("vendor"), account.get("auth")) in (("cursor", "api-key"), ("devin", "data-dir"))


def bundle_secret(account: dict[str, Any]) -> str | bytes:
    if account["vendor"] == "cursor":
        return read_cursor_key(account)
    try:
        return (devin_data_dir(account) / "devin" / "credentials.toml").read_bytes()
    except OSError:
        raise RuntimeError(f"credentials missing for {account['id']}") from None


def skip_reason(account: dict[str, Any], state: dict[str, Any]) -> str | None:
    if not leasable(account):
        return "login_not_leasable"
    if account.get("enabled", True) is False:
        return "disabled"
    record = state["accounts"].get(account["id"], {})
    if record.get("auth_ok") is False:
        return "auth_failed"
    if cooling(record):
        return "cooling"
    try:
        bundle_secret(account)
    except RuntimeError as error:
        return (
            "key_file_empty"
            if "empty" in str(error)
            else ("key_file_missing" if account["vendor"] == "cursor" else "credentials_missing")
        )
    return None


def write_bundle(
    out: Path, account: dict[str, Any], secret: str | bytes, trusts: list[str], meta: dict[str, str]
) -> None:
    written: list[Path] = []
    try:
        private_dir(out)
        private_dir(out / "data")
        if account["vendor"] == "cursor":
            env = f"CURSOR_API_KEY={shlex.quote(secret)}\nAGENT_CLI_CREDENTIAL_STORE=memory\n".encode()
        else:
            env = b""
            private_dir(out / "data" / "devin")
            private_dir(out / "data" / "devin" / "cli")
            credentials = out / "data" / "devin" / "credentials.toml"
            written.append(credentials)
            private_file(credentials, secret)
            trusted = out / "data" / "devin" / "cli" / "trusted_workspaces.json"
            written.append(trusted)
            private_file(
                trusted, (json.dumps({"trusted_paths": list(dict.fromkeys(trusts))}, indent=2) + "\n").encode()
            )
        env_path = out / "env"
        written.append(env_path)
        private_file(env_path, env)
        metadata = out / "lease.json"
        written.append(metadata)
        private_file(metadata, (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode())
    except OSError:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise RuntimeError("cannot write lease bundle") from None


def command_lease(args: argparse.Namespace) -> int:
    def apply(registry: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
        vendor_accounts = [a for a in registry if a.get("vendor") == args.vendor]
        if args.account:
            candidates = [a for a in vendor_accounts if a["id"] == args.account]
            if not candidates:
                lease_error(
                    "unknown_account", args.vendor, f"no {args.vendor} account {args.account} registered", args.json
                )
            if not leasable(candidates[0]):
                lease_error("not_leasable", args.vendor, f"account {args.account} is not leasable", args.json)
        else:
            candidates = [a for a in order_accounts(registry, state, args.vendor) if leasable(a)]
        selected = None
        secret = None
        for account in candidates:
            try:
                secret = bundle_secret(account)
            except RuntimeError:
                continue
            selected = account
            break
        if selected is None:
            skipped = [
                {"account": a["id"], "reason": skip_reason(a, state) or "credentials_missing"} for a in vendor_accounts
            ]
            if args.account:
                lease_error("no_account", args.vendor, f"no credentials for {args.account}", args.json, skipped)
            message = f"no {args.vendor} account can be leased"
            if skipped:
                message += ": " + ", ".join(f"{s['account']} ({s['reason']})" for s in skipped)
            if args.vendor == "cursor" and skipped and all(s["reason"] == "login_not_leasable" for s in skipped):
                message += (
                    "; keychain login stays on this machine; add an API-key account with "
                    "seat add <id> --vendor cursor --api-key-prompt"
                )
            lease_error("no_account", args.vendor, message, args.json, skipped)
        created = now()
        lease_id = "lease-" + uuid.uuid4().hex
        expiry = iso(created + timedelta(seconds=args.ttl))
        meta = {"lease": lease_id, "vendor": args.vendor, "account": selected["id"], "expires_at": expiry}
        try:
            write_bundle(Path(args.out).expanduser(), selected, secret, args.trust, meta)
        except RuntimeError:
            lease_error("out_failed", args.vendor, "cannot write lease bundle", args.json)
        state["leases"][lease_id] = {
            "vendor": args.vendor,
            "account": selected["id"],
            "for": args.for_id,
            "created_at": iso(created),
            "expires_at": expiry,
            "released_at": None,
            "outcome": None,
        }
        return meta

    try:
        meta = update_state(apply)
    except (OSError, ValueError, TypeError) as error:
        lease_error("state_failed", args.vendor, f"cannot read registry or state: {type(error).__name__}", args.json)
    lease_id = meta["lease"]
    append_ledger(
        [
            {
                "ts": iso(now()),
                "event": "lease",
                "session_id": lease_id,
                "lease": lease_id,
                "subagent_type": SEAT_FOR_VENDOR[args.vendor],
                "vendor": args.vendor,
                "account": meta["account"],
                "for": args.for_id,
                "expires_at": meta["expires_at"],
                "source": "seat",
            }
        ]
    )
    if args.json:
        print(json.dumps(meta, sort_keys=True))
    else:
        print(f"leased {meta['account']} ({args.vendor}) as {lease_id} until {meta['expires_at']}")
    return EXIT_OK
