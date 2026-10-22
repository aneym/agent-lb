from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from seat_storage import (  # pyrefly: ignore[missing-import]
    EXIT_CAPACITY_WAIT,
    EXIT_FAILED,
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    KILL_DRAIN_SECONDS,
    MUSE_STALL_SECONDS,
    RELEASE_BUDGET_SECONDS,
    ROUTE_EXIT_WAIT,
    SEAT_FOR_VENDOR,
    STOP_GRACE_SECONDS,
    STOP_POLL_SECONDS,
    STOP_SIGNALS,
    route_command,
)


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


def post_stall(vendor: str, cwd: Path, error: str) -> None:
  owner = os.environ.get("SEAT_OWNER_PANE") or os.environ.get("FACTORY_OWNER_PANE")
  if not owner or owner == "none":
    print("seat: STALLED with no owner pane to tell (set SEAT_OWNER_PANE)", file=sys.stderr)
    return
  poster = os.environ.get("SEAT_LANE_POST") or str(Path.home() / ".agent-rails/factory-runtime/bin/lane-post")
  sender = os.environ.get("HERDR_PANE_ID") or owner
  text = f"{vendor} seat stalled in {cwd}: {error}; the run was stopped and failed. Relaunch or reassign it."
  try:
    completed = subprocess.run(
      [poster, "post", "--to", owner, "--from", sender, "--kind", "task", text],
      capture_output=True,
      text=True,
      timeout=30,
      check=False,
    )
    print(f"seat: stall bulletin to {owner} rc={completed.returncode}", file=sys.stderr)
  except (OSError, subprocess.TimeoutExpired) as error_:
    print(f"seat: stall bulletin to {owner} failed: {error_}", file=sys.stderr)


def muse_call(call: dict[str, Any]) -> str:
  name = str(call.get("name") or "tool")
  try:
    args = json.loads(call.get("args") or "{}")
  except ValueError:
    args = {}
  first = next((v for v in args.values() if isinstance(v, str)), "") if isinstance(args, dict) else ""
  return f"{name} {' '.join(first.split())[:120]}".rstrip()


def muse_progress(line: str) -> str | None:
  try:
    event = json.loads(line)
  except ValueError:
    return None
  payload = event.get("payload") if isinstance(event, dict) else None
  if not isinstance(payload, dict):
    return None
  inner = payload.get("event") if isinstance(payload.get("event"), dict) else {}
  kind = inner.get("kind")
  if kind == "reasoning_summary_committed" and inner.get("text"):
    return "muse: " + " ".join(str(inner["text"]).split())[:200]
  if kind == "assistant_tool_calls_committed":
    calls = [muse_call(call) for call in inner.get("tool_calls") or [] if isinstance(call, dict)]
    return "muse: " + "; ".join(calls)[:240] if calls else None
  if kind == "todo_snapshot_updated":
    items = [i for i in inner.get("items") or [] if isinstance(i, dict)]
    active = next((i.get("text") for i in items if i.get("status") == "in_progress"), None)
    done = sum(1 for i in items if i.get("status") == "completed")
    return f"muse: todo {done}/{len(items)} done" + (f", now: {active}"[:160] if active else "")
  if kind == "status" and inner.get("message") and not re.search(r"attempt 1/|serialized", str(inner["message"])):
    return "muse: " + str(inner["message"])[:200]
  if payload.get("terminal"):
    return f"muse: run {payload['terminal']}"
  return None


class MuseTail:
  def __init__(self) -> None:
    self.started = False
    self.done = threading.Event()
    self.last = time.monotonic()
    self.stall_limit = float(os.environ.get("SEAT_STALL_SECONDS") or MUSE_STALL_SECONDS)

  def idle(self) -> float:
    return time.monotonic() - self.last

  def __call__(self, line: str) -> str | None:
    self.last = time.monotonic()
    if not self.started:
      try:
        stream = json.loads(line).get("stream") or {}
      except (ValueError, AttributeError):
        stream = {}
      if stream.get("kind") == "session" and stream.get("id"):
        self.started = True
        threading.Thread(target=self.follow, args=(str(stream["id"]),), daemon=True).start()
    shown = muse_progress(line)
    return shown if shown and (not self.started or "run " in shown) else None

  def follow(self, session_id: str) -> None:
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "muse" / "sessions"
    path = None
    while path is None and not self.done.wait(0.5):
      path = next(iter(root.glob(f"*/*/*/{session_id}/session.jsonl")), None)
    if path is None:
      return
    with path.open(encoding="utf-8", errors="replace") as stream:
      pending = ""
      while True:
        chunk = stream.readline()
        if chunk:
          self.last = time.monotonic()
          pending += chunk
          if not pending.endswith("\n"):
            continue
          shown = muse_progress(pending)
          pending = ""
          if shown and "run " not in shown:
            print(shown, file=sys.stderr, flush=True)
        elif self.done.wait(0.5):
          return

  def close(self) -> None:
    self.done.set()


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
