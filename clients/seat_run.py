from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from seat_environment import (  # pyrefly: ignore[missing-import]
    account_env,
    devin_trusted,
    local_proxy_vars,
)
from seat_models import (  # pyrefly: ignore[missing-import]
    build_command,
    check_cursor_model,
    resolve_model,
)
from seat_outcome import (  # pyrefly: ignore[missing-import]
    final_prompt,
    parse_outcome,
)
from seat_process import (  # pyrefly: ignore[missing-import]
    CapacityLease,
    MuseTail,
    RunControl,
    Stalled,
    post_stall,
    reserve_capacity,
    run_stoppable,
)
from seat_quota import (  # pyrefly: ignore[missing-import]
    refresh_quota,
)
from seat_storage import (  # pyrefly: ignore[missing-import]
    DEVIN_FREE_MODEL,
    EFFORT_SUFFIX,
    ERROR_CHARS,
    EXIT_FAILED,
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    MODEL_UNAVAILABLE_TTL,
    MUSE_MIN,
    MUSE_PIN,
    PROBE_TIMEOUT_SECONDS,
    SEAT_FOR_VENDOR,
    append_ledger,
    classify,
    cli_bin,
    cooldown_until,
    fail,
    iso,
    lease_accounts,
    load_registry,
    load_state,
    mark_limit,
    muse_version_ok,
    now,
    order_accounts,
    parse_ts,
    record_lease_usage,
    record_run,
    update_state,
)


def run_envelope(args: argparse.Namespace, model: str | None, run_id: str, error: str, **extra: Any) -> None:
  print(
    json.dumps(
      {
        "ok": False,
        "vendor": args.vendor,
        "account": None,
        "model": model,
        "run_id": run_id,
        "vendor_session_id": None,
        "wall_s": 0,
        "tokens_in": None,
        "tokens_out": None,
        "attempts": [],
        "error": error,
        **extra,
        "result": None,
      },
      indent=2,
    )
  )


def ready_candidates(
  args: argparse.Namespace, registry: list[dict[str, Any]], state: dict[str, Any], model: str, run_id: str
) -> tuple[list[dict[str, Any]] | None, int]:  # pyrefly: ignore[bad-return]  # pyrefly: ignore[bad-return]
  if args.account:
    candidates = [a for a in registry if a["id"] == args.account and a.get("vendor") == args.vendor]
    if not candidates:
      fail(f"no {args.vendor} account {args.account!r} registered", EXIT_NO_ACCOUNT)
  else:
    candidates = order_accounts(registry, state, args.vendor, model)
  if args.vendor == "cursor" and args.account:
    ready = order_accounts(registry, state, args.vendor, model)
    candidates = [account for account in candidates if account in ready]
  if candidates:
    return candidates, EXIT_OK
  misses = [
    record
    for account_id, record in state["accounts"].items()
    if any(a["id"] == account_id and a.get("vendor") == args.vendor for a in registry)
    and (until := parse_ts((record.get("model_unavailable") or {}).get(model)))
    and until > now()
    and (not args.account or account_id == args.account)
  ]
  if misses:
    error = (misses[-1].get("model_unavailable_text") or {}).get(model) or f"Cannot use this model: {model}"
    run_envelope(args, model, run_id, error)
    return None, EXIT_NO_ACCOUNT
  fail(f"no {args.vendor} account is ready (see `seat accounts`)", EXIT_NO_ACCOUNT)


def command_run(args: argparse.Namespace) -> int:
  if args.prompt_file:
    prompt_path = Path(args.prompt_file).expanduser()
    prompt = prompt_path.read_text(encoding="utf-8")
  else:
    prompt = " ".join(args.prompt).strip()
  if not prompt:
    fail("empty prompt: pass --prompt-file or a prompt after --")
  if args.print_prompt:
    sys.stdout.write(final_prompt(prompt, args.task_class))
    return EXIT_OK
  cwd = Path(args.cwd or os.getcwd()).expanduser().resolve()
  if args.vendor == "muse":
    version = subprocess.run(
      [cli_bin("muse"), "--version"],
      capture_output=True,
      text=True,
      env={**os.environ, "MUSE_NO_AUTO_UPDATE": "1"},
      timeout=PROBE_TIMEOUT_SECONDS,
      check=False,
    )
    if version.returncode or not muse_version_ok(version.stdout):
      fail(
        f"Muse seat requires Muse Code {MUSE_MIN[0]}.x at or above {MUSE_PIN}; "
        f"found {version.stdout.strip() or 'none'}"
      )
    print(f"seat: {version.stdout.strip()}", file=sys.stderr, flush=True)
  registry = load_registry() + lease_accounts(args.vendor)
  state = load_state()
  run_id = f"seat-{uuid.uuid4()}"
  if not args.task_class or args.account:
    model = resolve_model(args.vendor, args.model)
    candidates, code = ready_candidates(args, registry, state, model, run_id)
    if candidates is None:
      return code
    if args.vendor == "cursor":
      candidates = check_cursor_model(model, candidates)
    why = "no --class" if not args.task_class else "--account pins one account; a lease is its own admission"
    return run_on_accounts(args, prompt, cwd, model, candidates, run_id, None, why, None)
  if not order_accounts(registry, state, args.vendor):
    fail(f"no {args.vendor} account is ready (see `seat accounts`)", EXIT_NO_ACCOUNT)
  control = RunControl()
  control.install()
  host = os.environ.get("ROUTE_HOST") or socket.gethostname()
  code, reservation = reserve_capacity(
    args.task_class, args.vendor, f"seat/{host}/{run_id}", args.author_vendor, args.model, control
  )
  if code == EXIT_OK and control.stop_reason():
    CapacityLease(reservation, control).release("cancelled")
    code = EXIT_FAILED
  if code != EXIT_OK:
    control.restore()
    run_envelope(
      args,
      args.model,
      run_id,
      control.stop_reason() or f"capacity: {reservation.get('reason') or 'no capacity'}",
      wait=reservation.get("wait"),
    )
    return 128 + control.signals[0] if control.signals else code
  code = EXIT_FAILED
  lease: CapacityLease | None = None
  try:
    lease = CapacityLease(reservation, control)
    lease.start()
    code = run_reserved(args, prompt, cwd, registry, state, run_id, reservation, control)
  except SystemExit as stop:
    code = stop.code if isinstance(stop.code, int) else EXIT_FAILED
  finally:
    outcome = "cancelled" if control.signals else "ok" if code == EXIT_OK and not control.lost else "failed"
    if lease is None:
      lease = CapacityLease(reservation, control)
    lease.release(outcome)
    control.restore()
  return 128 + control.signals[0] if control.signals else code


def same_model(wanted: str, *names: str | None) -> bool:
  base = EFFORT_SUFFIX.sub("", wanted)
  return any(name and (wanted == name or base == EFFORT_SUFFIX.sub("", name)) for name in names)


def run_reserved(
  args: argparse.Namespace,
  prompt: str,
  cwd: Path,
  registry: list[dict[str, Any]],
  state: dict[str, Any],
  run_id: str,
  reservation: dict[str, Any],
  control: RunControl,
) -> int:
  reserved = reservation.get("model")
  if not isinstance(reserved, str) or not reserved:
    run_envelope(args, args.model, run_id, "capacity: the reservation names no model")
    return EXIT_FAILED
  if not same_model(args.model, reserved, reservation.get("alias")):
    caller = resolve_model(args.vendor, args.model, control)
    if stopped := control.stop_reason():
      run_envelope(args, args.model, run_id, stopped)
      return EXIT_FAILED
    if not same_model(caller, reserved):
      run_envelope(
        args,
        caller,
        run_id,
        f"model: {args.model} is {caller}, but the {reservation.get('rung')} reservation holds "
        f"{reserved}; seat runs only the reserved model",
      )
      return EXIT_NO_ACCOUNT
  if args.vendor == "devin" and not DEVIN_FREE_MODEL.match(reserved):
    fail(f"{reserved} is billed per token on Devin; the devin seat runs only SWE-2 (swe-2-high|medium|max)")
  candidates, code = ready_candidates(args, registry, state, reserved, run_id)
  if candidates is None:
    return code
  if args.vendor == "cursor":
    candidates = check_cursor_model(reserved, candidates, control)
  return run_on_accounts(
    args, prompt, cwd, reserved, candidates, run_id, reservation["reservation_id"], None, control
  )


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
  append_ledger(
    [{**base, **unreserved, "ts": iso(now()), "event": "dispatch", "accounts": [a["id"] for a in candidates]}]
  )

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
    prompt_file.write_text(
      final_prompt(prompt, args.task_class)
      + (
        "\nRead-only task: do not edit files or perform any state-changing operations.\n"
        if args.vendor == "muse" and args.mode == "ask"
        else ""
      ),
      encoding="utf-8",
    )
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
      command = build_command(
        args.vendor, model, args.mode, prompt_file, export_file, getattr(args, "effort", "medium")
      )
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
          attempts.append(
            {
              "account": account["id"],
              "outcome": "cancelled" if control.signals else "reservation_lost",
              "wall_s": wall,
            }
          )
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
