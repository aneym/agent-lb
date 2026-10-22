from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from clients.seat_capacity import CapacityLease, reserve_capacity
from clients.seat_constants import (
    DEVIN_FREE_MODEL,
    EXIT_FAILED,
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    MUSE_MIN,
    MUSE_PIN,
    PROBE_TIMEOUT_SECONDS,
)
from clients.seat_credentials import muse_version_ok
from clients.seat_execution import run_on_accounts
from clients.seat_models import check_cursor_model, resolve_model, same_model
from clients.seat_process import RunControl
from clients.seat_prompts import final_prompt
from clients.seat_selection import order_accounts, ready_candidates, run_envelope
from clients.seat_state import cli_bin, fail, lease_accounts, load_registry, load_state


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
