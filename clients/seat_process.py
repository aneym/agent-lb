from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from clients.seat_constants import KILL_DRAIN_SECONDS, STOP_GRACE_SECONDS, STOP_POLL_SECONDS, STOP_SIGNALS


class RunControl:
    def __init__(self) -> None:
        self.signals: list[int] = []
        self.first_signal_at: float | None = None
        self.lost: str | None = None
        self.previous: dict[int, Any] = {}

    def record(self, signum: int, _frame: Any) -> None:
        self.signals.append(signum)
        if self.first_signal_at is None:
            self.first_signal_at = time.monotonic()

    def install(self) -> None:
        for signum in STOP_SIGNALS:
            self.previous[signum] = signal.signal(signum, self.record)

    def restore(self) -> None:
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)

    def stop_reason(self) -> str | None:
        if self.signals:
            return f"cancelled by signal {signal.Signals(self.signals[0]).name}"
        return self.lost


def run_stoppable(
    command: list[str],
    control: RunControl | None,
    *,
    input: str | None,
    timeout: float,
    echo: Any = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    if control is None and echo is None:
        return subprocess.run(
            command, input=input, capture_output=True, text=True, timeout=timeout, check=False, **kwargs
        )
    control = control or RunControl()
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, **kwargs
    )
    stop = GroupStop(process.pid, control, time.monotonic() + timeout)
    if echo is not None:
        stdout, stderr = echo_until_exit(process, stop, input, echo)
    else:
        pending = input
        while True:
            try:
                stdout, stderr = process.communicate(pending, timeout=STOP_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pending = None
            stop.step(leader_done=False)
    stop.drain()
    if stop.timed_out:
        raise subprocess.TimeoutExpired(command, timeout, stdout, stderr)
    if stop.stalled:
        raise Stalled(stop.stalled)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def echo_until_exit(process: subprocess.Popen[str], stop: GroupStop, input: str | None, echo: Any) -> tuple[str, str]:
    out: list[str] = []
    err: list[str] = []

    def pump(source: Any, sink: list[str], show: bool) -> None:
        for line in source:
            sink.append(line)
            if show:
                try:
                    shown = echo(line)
                except Exception:
                    shown = None
                if shown:
                    print(shown, file=sys.stderr, flush=True)

    readers = [
        threading.Thread(target=pump, args=(process.stdout, out, True), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, err, False), daemon=True),
    ]
    for reader in readers:
        reader.start()
    if input is not None and process.stdin is not None:
        try:
            process.stdin.write(input)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    limit = getattr(echo, "stall_limit", None)
    while True:
        try:
            process.wait(timeout=STOP_POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            if limit and stop.stalled is None and echo.idle() >= limit:
                stop.stalled = f"no vendor output for {echo.idle():.0f}s (stall limit {limit:.0f}s)"
                print(f"seat: STALLED: {stop.stalled}; stopping the vendor CLI", file=sys.stderr, flush=True)
                stop.send(signal.SIGTERM)
                stop.termed_at = time.monotonic()
            stop.step(leader_done=False)
    for reader in readers:
        reader.join(timeout=5)
    close = getattr(echo, "close", None)
    if close:
        close()
    return "".join(out), "".join(err)


class Stalled(RuntimeError):
    pass


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class GroupStop:
    def __init__(self, pgid: int, control: RunControl, deadline: float) -> None:
        self.pgid = pgid
        self.control = control
        self.deadline = deadline
        self.termed_at: float | None = None
        self.killed_at: float | None = None
        self.timed_out = False
        self.stalled: str | None = None

    def send(self, signum: int) -> None:
        try:
            os.killpg(self.pgid, signum)
        except (ProcessLookupError, PermissionError):
            pass

    def step(self, *, leader_done: bool) -> None:
        clock = time.monotonic()
        if self.killed_at is not None:
            return
        if clock >= self.deadline:
            self.timed_out = not leader_done
            self.send(signal.SIGKILL)
            self.killed_at = clock
        elif self.termed_at is None and (leader_done or self.control.stop_reason()):
            self.send(signal.SIGTERM)
            self.termed_at = clock
        elif self.termed_at is not None and (
            len(self.control.signals) > 1 or clock - self.termed_at >= STOP_GRACE_SECONDS
        ):
            self.send(signal.SIGKILL)
            self.killed_at = clock

    def drain(self) -> None:
        while group_alive(self.pgid):
            self.step(leader_done=True)
            if self.killed_at is not None and time.monotonic() - self.killed_at > KILL_DRAIN_SECONDS:
                print(
                    f"seat: process group {self.pgid} is still present {KILL_DRAIN_SECONDS:.0f}s after SIGKILL "
                    "(an unreaped zombie or uninterruptible I/O); releasing anyway",
                    file=sys.stderr,
                )
                return
            if self.killed_at is not None:
                self.send(signal.SIGKILL)
            time.sleep(STOP_POLL_SECONDS)
