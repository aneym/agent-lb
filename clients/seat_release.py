from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, cast

from clients.seat_constants import AUTH_RE, EXIT_NO_ACCOUNT, EXIT_OK, LIMIT_RE, RETRY_AFTER_RE, SEAT_FOR_VENDOR
from clients.seat_outcomes import classify
from clients.seat_probe import run_probe
from clients.seat_selection import mark_limit
from clients.seat_state import iso, load_registry, load_state, now, parse_ts, update_state
from clients.seat_usage import append_ledger, record_run, release_usage


def release_error(lease_id: str, json_output: bool) -> Any:
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
    diagnostic = f"{kind} in seat log: {cast(re.Match[str], match).group(0)}"
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
