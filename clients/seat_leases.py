from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, NoReturn

from seat_environment import (  # pyrefly: ignore[missing-import]
    devin_data_dir,
    read_cursor_key,
)
from seat_probe import (  # pyrefly: ignore[missing-import]
    run_probe,
)
from seat_storage import (  # pyrefly: ignore[missing-import]
    AUTH_RE,
    EXIT_FAILED,
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    LIMIT_RE,
    RETRY_AFTER_RE,
    SEAT_FOR_VENDOR,
    append_ledger,
    classify,
    cooling,
    iso,
    load_registry,
    load_state,
    mark_limit,
    now,
    order_accounts,
    parse_ts,
    private_dir,
    private_file,
    record_run,
    release_usage,
    update_state,
)


def lease_error(
    code: str, vendor: str, message: str, json_output: bool, skipped: list[dict[str, str]] | None = None
) -> NoReturn:
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
            env = f"CURSOR_API_KEY={shlex.quote(secret)}\nAGENT_CLI_CREDENTIAL_STORE=memory\n".encode()  # pyrefly: ignore[bad-argument-type]
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
            write_bundle(Path(args.out).expanduser(), selected, secret, args.trust, meta)  # pyrefly: ignore[bad-argument-type]
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


def release_error(lease_id: str, json_output: bool) -> NoReturn:
    message = f"no lease {lease_id}"
    print(f"seat: {message}", file=sys.stderr)
    if json_output:
        print(json.dumps({"error": "unknown_lease", "message": message}, sort_keys=True))
    raise SystemExit(EXIT_NO_ACCOUNT)


def release_diagnostic(kind: str, text: str, reported: bool) -> str:
    if reported:
        return f"outcome {kind} reported by caller"
    if kind in ("model_unavailable", "transient_infra"):
        return f"{kind} in seat log"
    if kind == "error":
        return "seat failed; no limit or auth text in its log"
    match = (AUTH_RE if kind == "auth" else LIMIT_RE).search(text)
    diagnostic = f"{kind} in seat log: {match.group(0)}"
    retry = RETRY_AFTER_RE.search(text) if kind == "limit" else None
    return diagnostic + (f"; {retry.group(0)}" if retry else "")


def release_log(path: str | None) -> tuple[str, bool]:
    if path is None:
        return "", False
    try:
        with Path(path).expanduser().open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            text = stream.read().decode("utf-8", errors="replace")
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text), True
    except OSError:
        return "", False


def command_release(args: argparse.Namespace) -> int:
    text, log_read = release_log(args.log)
    usage = release_usage(args.model, args.usage_file)
    outcome = args.outcome or ((classify(text) or "error") if log_read else "unknown")
    if outcome in ("model_unavailable", "transient_infra"):
        outcome = "error"
    snapshot = load_state().get("leases", {}).get(args.lease)
    if snapshot is None:
        release_error(args.lease, args.json)

    def already(lease: dict[str, Any]) -> dict[str, Any]:
        return {
            "lease": args.lease,
            "released": False,
            "reason": "already_released",
            "outcome": lease["outcome"],
            "released_at": lease["released_at"],
        }

    if snapshot.get("released_at") is not None:
        result = already(snapshot)
    else:
        probe_status = None
        if outcome == "auth":
            account = next((a for a in load_registry() if a["id"] == snapshot["account"]), None)
            if account is not None:
                probe, probe_text = run_probe(account)
                if probe.get("auth_ok") is True:
                    probe_status = "ok"
                elif probe_text is not None and classify(probe_text) == "auth" and probe.get("auth_ok") is False:
                    probe_status = "rejected"
            probe_status = probe_status or "inconclusive"

        def apply(registry: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
            lease = state["leases"].get(args.lease)
            if lease is None:
                release_error(args.lease, args.json)
            if lease.get("released_at") is not None:
                return already(lease)
            stamp = now()
            ts = iso(stamp)
            wall = round((stamp - (parse_ts(lease["created_at"]) or stamp)).total_seconds(), 1)
            expired = (parse_ts(lease["expires_at"]) or stamp) <= stamp
            lease["released_at"] = ts
            lease["outcome"] = outcome
            record = None
            ok = True if outcome == "ok" else (None if outcome == "unknown" else False)
            if any(a["id"] == lease["account"] for a in registry):
                record = state["accounts"].setdefault(lease["account"], {})
                record["last_run_at"] = ts
                record_run(record, {"ts": ts, **usage, "ok": ok, "wall_s": wall, "run_id": args.lease})
                if outcome == "ok":
                    record["last_error"] = None
                    record["auth_ok"] = True
                elif outcome in ("limit", "auth", "error"):
                    kind = "auth_unconfirmed" if outcome == "auth" and probe_status != "rejected" else outcome
                    record["last_error"] = {
                        "ts": ts,
                        "kind": kind,
                        "text": release_diagnostic(outcome, text, args.outcome is not None),
                    }
                    if outcome == "limit":
                        mark_limit(record, lease["vendor"], args.model or "", text)
                    elif outcome == "auth" and probe_status in ("rejected", "ok"):
                        record["auth_ok"] = probe_status == "ok"
                        record["auth_checked_at"] = ts
                        record["auth_error"] = (
                            "rejected by probe after a leased run" if probe_status == "rejected" else None
                        )
            result = {
                "lease": args.lease,
                "vendor": lease["vendor"],
                "account": lease["account"],
                "for": lease["for"],
                "released": True,
                "outcome": outcome,
                "expired": expired,
                "log_read": log_read,
                "auth_probe": probe_status if outcome == "auth" else None,
                "auth_confirmed": probe_status == "rejected" if outcome == "auth" else None,
                "auth_ok": record.get("auth_ok") if record else None,
                "cooldown_until": record.get("cooldown_until") if record else None,
            }
            result["ledger"] = {
                "ts": ts,
                "event": "release",
                **usage,
                "session_id": args.lease,
                "lease": args.lease,
                "subagent_type": SEAT_FOR_VENDOR[lease["vendor"]],
                "vendor": lease["vendor"],
                "account": lease["account"],
                "for": lease["for"],
                "outcome": outcome,
                "ok": ok,
                "wall_s": wall,
                "expired": expired,
                "source": "seat",
            }
            return result

        result = update_state(apply)
    ledger = result.pop("ledger", None)
    if ledger:
        append_ledger([ledger])
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["released"]:
        print(f"released {args.lease} ({result['account']}): {outcome}")
    else:
        print(f"lease {args.lease} already released")
    return EXIT_OK
