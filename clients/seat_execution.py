from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from clients.seat_constants import (
    ERROR_CHARS,
    EXIT_FAILED,
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    MODEL_UNAVAILABLE_TTL,
    SEAT_FOR_VENDOR,
)
from clients.seat_credentials import account_env, devin_trusted, local_proxy_vars
from clients.seat_models import build_command
from clients.seat_muse import MuseTail, post_stall
from clients.seat_outcomes import classify, cooldown_until, parse_outcome
from clients.seat_process import RunControl, Stalled, run_stoppable
from clients.seat_prompts import final_prompt
from clients.seat_quota import refresh_quota
from clients.seat_selection import mark_limit
from clients.seat_state import iso, now, update_state
from clients.seat_usage import append_ledger, record_lease_usage, record_run


def run_on_accounts(
    args: argparse.Namespace,
    prompt: str,
    cwd: Path,
    model: str,
    candidates: list[dict[str, Any]],
    run_id: str,
    reservation_id: str | None,
    why: str | None,
    control: RunControl | None,
) -> int:
    seat = SEAT_FOR_VENDOR[args.vendor]
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    base = {
        "session_id": run_id,
        "name": None,
        "subagent_type": seat,
        "task_class": args.task_class,
        "model": model,
        "vendor": args.vendor,
        "source": "seat",
        "prompt_sha256": prompt_sha,
        "cwd": str(cwd),
        "reservation": reservation_id,
    }
    unreserved = {} if reservation_id else {"why": why}
    append_ledger([{**base, **unreserved, "ts": iso(now()), "event": "dispatch",
                    "accounts": [a["id"] for a in candidates]}])

    attempts: list[dict[str, Any]] = []
    changes: dict[str, dict[str, Any]] = {}
    outcome: dict[str, Any] = {}
    account: dict[str, Any] = candidates[0]
    started_all = time.monotonic()
    removed_proxies = local_proxy_vars(dict(os.environ))
    if removed_proxies:
        print(f"seat: debug: dropped local proxy variables: {', '.join(removed_proxies)}", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="seat-") as scratch:
        prompt_file = Path(scratch) / "prompt.md"
        prompt_file.write_text(final_prompt(prompt, args.task_class) +
                               ("\nRead-only task: do not edit files or perform any state-changing operations.\n"
                                if args.vendor == "muse" and args.mode == "ask" else ""), encoding="utf-8")
        limited_plans: set[str] = set()
        kind: str | None = None
        for account in candidates:
            if control and (stopped := control.stop_reason()):
                outcome, kind = {"ok": False, "error": stopped}, "stopped"
                break
            if args.vendor == "cursor" and account.get("plan") in limited_plans:
                continue
            if args.vendor == "devin" and not devin_trusted(account, cwd):
                attempts.append({"account": account["id"], "outcome": "untrusted_workspace"})
                outcome = {
                    "ok": False,
                    "error": f"{cwd} is not a trusted Devin workspace for {account['id']}; "
                    "open `devin` there once interactively to trust it",
                }
                continue
            export_file = Path(scratch) / f"{account['id']}.export.json"
            command = build_command(args.vendor, model, args.mode, prompt_file, export_file,
                                    getattr(args, "effort", "medium"))
            for retry in range(2):
                if control and (stopped := control.stop_reason()):
                    outcome, kind = {"ok": False, "error": stopped}, "stopped"
                    break
                started = time.monotonic()
                try:
                    completed = run_stoppable(
                        command,
                        control,
                        cwd=cwd,
                        env=account_env(account),
                        input=prompt_file.read_text(encoding="utf-8") if args.vendor == "cursor" else None,
                        stdin=None if args.vendor == "cursor" else subprocess.DEVNULL,
                        timeout=args.timeout,
                        echo=MuseTail() if args.vendor == "muse" else None,
                    )
                    outcome = parse_outcome(args.vendor, completed, export_file)
                    try:
                        refresh_quota(account, outcome.get("quota"))
                    except Exception:
                        print("seat: quota refresh failed", file=sys.stderr)
                except subprocess.TimeoutExpired:
                    outcome = {"ok": False, "error": f"timeout after {args.timeout:.0f}s"}
                except Stalled as error:
                    outcome = {"ok": False, "error": f"stalled: {error}"}
                    post_stall(args.vendor, cwd, outcome["error"])
                except (OSError, RuntimeError) as error:
                    outcome = {"ok": False, "error": f"{type(error).__name__}: {error}"[:ERROR_CHARS]}
                wall = round(time.monotonic() - started, 1)
                if control and (stopped := control.stop_reason()):
                    outcome, kind = {"ok": False, "error": stopped}, "stopped"
                    attempts.append({"account": account["id"], "outcome": "cancelled" if control.signals
                                     else "reservation_lost", "wall_s": wall})
                    break
                kind = None if outcome["ok"] else classify(outcome.get("error") or "")
                change = changes.setdefault(account["id"], {"fields": {}, "runs": []})
                fields = change["fields"]
                fields["last_run_at"] = iso(now())
                change["runs"].append(
                    {
                        "ts": iso(now()),
                        "model": model,
                        "ok": outcome["ok"],
                        "wall_s": wall,
                        "tokens_in": outcome.get("tokens_in"),
                        "tokens_out": outcome.get("tokens_out"),
                        "cache_read": outcome.get("cache_read_tokens"),
                        "run_id": run_id,
                    }
                )
                attempts.append(
                    {"account": account["id"], "outcome": "ok" if outcome["ok"] else (kind or "error"), "wall_s": wall}
                )
                if account.get("auth") == "lease":
                    record_lease_usage(
                        account,
                        {
                            "ts": iso(now()),
                            "model": model,
                            "ok": outcome.get("ok"),
                            "wall_s": wall,
                            "tokens_in": outcome.get("tokens_in"),
                            "tokens_out": outcome.get("tokens_out"),
                            "cache_read_tokens": outcome.get("cache_read_tokens"),
                        },
                    )
                if outcome["ok"]:
                    fields["last_error"] = None
                    fields["auth_ok"] = True
                    break
                fields["last_error"] = {"ts": iso(now()), "kind": kind or "error", "text": outcome.get("error")}
                if kind == "limit":
                    mark_limit(fields, args.vendor, model, outcome.get("error") or "")
                    if args.vendor == "cursor" and account.get("plan"):
                        limited_plans.add(account["plan"])
                elif kind == "auth":
                    fields["auth_ok"] = False
                    if args.vendor == "cursor":
                        fields["cooldown_until"] = iso(cooldown_until(outcome.get("error") or ""))
                elif kind == "model_unavailable":
                    until = iso(now() + MODEL_UNAVAILABLE_TTL)
                    fields.setdefault("model_unavailable", {})[model] = until
                    vendor_text = outcome.get("error") or f"Cannot use this model: {model}"
                    fields.setdefault("model_unavailable_text", {})[model] = vendor_text

                    def mark_unavailable(registry: list[dict[str, Any]], state: dict[str, Any]) -> None:
                        if any(entry["id"] == account["id"] for entry in registry):
                            record = state["accounts"].setdefault(account["id"], {})
                            record.setdefault("model_unavailable", {})[model] = until
                            record.setdefault("model_unavailable_text", {})[model] = vendor_text

                    update_state(mark_unavailable)
                elif kind == "transient_infra":
                    if retry == 0:
                        lo, hi = map(float, os.environ.get("SEAT_RETRY_JITTER_S", "5,15").split(","))
                        time.sleep(random.uniform(lo, hi))
                        continue
                else:
                    break

                break
            if outcome.get("ok") or kind not in ("limit", "auth", "model_unavailable", "transient_infra"):
                break

    def apply_run(fresh_registry: list[dict[str, Any]], fresh: dict[str, Any]) -> None:
        known = {entry["id"] for entry in fresh_registry}
        for account_id, change in changes.items():
            if account_id not in known:
                continue
            record = fresh["accounts"].setdefault(account_id, {})
            fields = change["fields"].copy()
            for key in ("cooldowns", "limits", "model_unavailable", "model_unavailable_text"):
                if key in fields:
                    record.setdefault(key, {}).update(fields.pop(key))
            record.update(fields)
            for run in change["runs"]:
                record_run(record, run)

    update_state(apply_run)
    wall_all = round(time.monotonic() - started_all, 1)
    closeout = {
        **base,
        "ts": iso(now()),
        "event": "closeout",

        "account": attempts[-1]["account"] if attempts else None,
        "attempts": attempts,
        "vendor_session_id": outcome.get("session_id"),
        "duration_s": wall_all,
        "wall_s": wall_all,
        "tokens_in": outcome.get("tokens_in"),
        "tokens_out": outcome.get("tokens_out"),
        "cache_read_tokens": outcome.get("cache_read_tokens"),
        "ok": bool(outcome.get("ok")),
        "error": outcome.get("error"),
    }
    if args.vendor == "muse":
        closeout["usage_source"] = outcome.get("usage_source", "unavailable")
    append_ledger([closeout])
    envelope = {
        "ok": closeout["ok"],
        "vendor": args.vendor,
        "account": closeout["account"],
        "model": model,
        "run_id": run_id,
        "vendor_session_id": closeout["vendor_session_id"],
        "wall_s": wall_all,
        "tokens_in": closeout["tokens_in"],
        "tokens_out": closeout["tokens_out"],
        "attempts": attempts,
        "error": closeout["error"],
        "result": outcome.get("result"),
    }
    if args.vendor == "muse":
        envelope["usage_source"] = closeout["usage_source"]
    print(json.dumps(envelope, indent=2))
    if closeout["ok"]:
        return EXIT_OK
    exhausted = attempts and all(
        a["outcome"] in ("limit", "auth", "untrusted_workspace", "model_unavailable", "transient_infra")
        for a in attempts
    )
    return EXIT_NO_ACCOUNT if exhausted else EXIT_FAILED
