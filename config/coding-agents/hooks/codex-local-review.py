#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PENDING = 3


def contract_dir(value):
    path = Path(os.path.realpath(value))
    if path.name == "contract.md":
        path = path.parent
    contract = path / "contract.md"
    try:
        with contract.open() as stream:
            header = stream.readline()
    except OSError:
        header = ""
    if path.parent.name != "seat-contracts" or header != f"seat-contract: {path.name}\n":
        sys.exit(f"codex-local: {value} is not a contract printed by forward-contract.py")
    return path


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def progress(directory):
    logs = sorted((directory / "companion" / "state").glob("*/jobs/task-*.log"))
    if not logs:
        return "no companion job yet"
    lines = logs[-1].read_text(errors="replace").strip().splitlines()
    return f"{logs[-1].stem}: {lines[-1][:200] if lines else 'started'}"


def wait(directory, seconds):
    review = directory / "review"
    if not review.is_dir():
        sys.exit(f"codex-local: no review was started for {directory}")
    deadline = time.monotonic() + seconds
    started = float((review / "started").read_text())
    pid = int((review / "pid").read_text())
    while not (review / "rc").exists():
        if not alive(pid) and not (review / "rc").exists():
            sys.stderr.write((review / "stderr").read_text(errors="replace"))
            print("codex-local: review runner exited without a result", file=sys.stderr)
            return 1
        if time.monotonic() >= deadline:
            print(
                f"codex-local: review still running after {int(time.time() - started)}s ({progress(directory)}); "
                f"wait again with: bash {Path(__file__).resolve().parent}/codex-local.sh wait {directory}/contract.md",
                file=sys.stderr,
            )
            return PENDING
        time.sleep(2)
    sys.stderr.write((review / "stderr").read_text(errors="replace"))
    sys.stderr.flush()
    sys.stdout.write((review / "stdout").read_text(errors="replace"))
    sys.stdout.flush()
    return int((review / "rc").read_text())


def start(directory, command, seconds):
    review = directory / "review"
    try:
        review.mkdir(mode=0o700)
    except FileExistsError:
        sys.exit(f"codex-local: a review already runs for {directory}; use codex-local.sh wait {directory}/contract.md")
    (review / "started").write_text(f"{time.time()}\n")
    runner = subprocess.Popen(
        [sys.executable, __file__, "run", str(directory), "--", *command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    (review / "pid").write_text(f"{runner.pid}\n")
    return wait(directory, seconds)


def run(directory, command):
    review = directory / "review"
    with (review / "stdout").open("wb") as out, (review / "stderr").open("wb") as err:
        rc = subprocess.call(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
    pending = review / f".rc.{os.getpid()}"
    pending.write_text(f"{rc}\n")
    os.replace(pending, review / "rc")
    return rc


parser = argparse.ArgumentParser(prog="codex-local-review.py")
parser.add_argument("mode", choices=("start", "wait", "run"))
parser.add_argument("contract")
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
directory = contract_dir(args.contract)
window = float(os.environ.get("CODEX_LOCAL_WAIT_SECONDS") or 240)
command = args.command[1:] if args.command[:1] == ["--"] else args.command
if args.mode == "wait":
    sys.exit(wait(directory, window))
if not command:
    parser.error(f"{args.mode} requires a command after --")
sys.exit(start(directory, command, window) if args.mode == "start" else run(directory, command))
