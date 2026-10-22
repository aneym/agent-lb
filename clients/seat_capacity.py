from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from clients.seat_constants import (
    EXIT_CAPACITY_WAIT,
    EXIT_FAILED,
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    RELEASE_BUDGET_SECONDS,
    ROUTE_EXIT_WAIT,
    SEAT_FOR_VENDOR,
    STOP_SIGNALS,
)
from clients.seat_process import RunControl, run_stoppable
from clients.seat_state import route_command


def reserve_capacity(
    task_class: str, vendor: str, job: str, author_vendor: str | None, model: str, control: RunControl
) -> tuple[int, dict[str, Any]]:
    argv = route_command(
        "reserve",
        task_class,
        *(["--author-vendor", author_vendor] if author_vendor else []),
        "--job",
        job,
        "--prefer",
        SEAT_FOR_VENDOR[vendor],
        "--model",
        model,
        "--reason",
        "seat run",
        "--json",
    )
    try:
        completed = run_stoppable(argv, control, input=None, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as error:
        return EXIT_FAILED, {"status": "error", "reason": f"route reserve failed: {error}"}
    try:
        body = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        body = {}
    body = body if isinstance(body, dict) else {}
    status = body.get("status")
    if completed.returncode == EXIT_OK and status == "reserved" and body.get("reservation_id"):
        return EXIT_OK, body
    if stopped := control.stop_reason():
        return EXIT_FAILED, {"status": "cancelled", "reason": stopped}
    if completed.returncode == ROUTE_EXIT_WAIT and status == "wait":
        return EXIT_CAPACITY_WAIT, body
    if completed.returncode == EXIT_NO_ACCOUNT and status == "refused":
        return EXIT_NO_ACCOUNT, body
    reason = (
        body.get("reason")
        or body.get("message")
        or completed.stderr.strip()
        or f"route reserve exited {completed.returncode}"
    )
    return EXIT_FAILED, {**body, "reason": reason}


class CapacityLease:
    def __init__(self, reservation: dict[str, Any], control: RunControl) -> None:
        self.reservation_id = reservation["reservation_id"]
        self.interval = max(1, int(reservation.get("heartbeat_s") or 300))
        self.control = control
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._beat, name="seat-reservation-heartbeat", daemon=True)

    def _beat(self) -> None:
        while not self.stopped.wait(self.interval):
            try:
                completed = subprocess.run(
                    route_command("heartbeat", self.reservation_id, "--json"),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    start_new_session=True,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                print(f"seat: route heartbeat {self.reservation_id} failed: {error}", file=sys.stderr)
                continue
            if completed.returncode == EXIT_NO_ACCOUNT:
                self.control.lost = f"capacity: reservation {self.reservation_id} was lost; the vendor CLI was stopped"
                print(f"seat: reservation {self.reservation_id} is gone; stopping the vendor CLI", file=sys.stderr)
                return
            if completed.returncode != EXIT_OK:
                print(f"seat: route heartbeat {self.reservation_id} exited {completed.returncode}", file=sys.stderr)

    def start(self) -> None:
        self.thread.start()

    def release(self, outcome: str) -> None:
        until = time.monotonic() + RELEASE_BUDGET_SECONDS
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        masked = hasattr(signal, "pthread_sigmask")
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS) if masked else None
        try:
            detail = "not attempted"
            for _attempt in range(2):
                left = until - time.monotonic()
                if left < 1:
                    break
                try:
                    completed = subprocess.run(
                        route_command("release", self.reservation_id, "--outcome", outcome, "--json"),
                        capture_output=True,
                        text=True,
                        timeout=left,
                        check=False,
                        start_new_session=True,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    detail = f"failed: {error}"
                    continue
                if completed.returncode in (EXIT_OK, EXIT_NO_ACCOUNT):
                    return
                detail = f"exited {completed.returncode}"
            print(f"seat: route release {self.reservation_id} {detail}; it expires at its TTL", file=sys.stderr)
        finally:
            if previous is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous)
