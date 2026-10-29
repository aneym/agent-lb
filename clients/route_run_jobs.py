from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from route_reserve_state import new_ulid  # pyrefly: ignore [missing-import]
from route_versioned_rules import now_iso, setup_error  # pyrefly: ignore [missing-import]


def run_path(decision_id: str, suffix: str) -> Path:
    if not re.fullmatch(r"dcn_[A-Za-z0-9]+", decision_id):
        raise ValueError("invalid decision id")
    return Path.home() / ".agent-lb/logs/route-run" / (decision_id + suffix)


def save_run(row: dict) -> None:
    path = run_path(row["decision_id"], ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def launch_run(args: argparse.Namespace, cwd: Path, brief: str) -> int:
    decision_id = f"dcn_{new_ulid()}"
    row = {
        "decision_id": decision_id,
        "status": "queued",
        "ts": now_iso(),
        "log_path": str(run_path(decision_id, ".log")),
    }
    save_run(row)
    options = {**vars(args), "bg": False, "detached": True, "decision_id": decision_id, "json": True}
    options.pop("handler", None)
    options["job"] = args.job or f"route-run-{decision_id}"
    options["cwd"] = str(cwd)
    options["brief"] = None
    try:
        with run_path(decision_id, ".supervisor.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("route_run_worker.py"))],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                start_new_session=True,
                text=True,
            )
        row["pid"] = process.pid
        save_run(row)
        assert process.stdin is not None
        process.stdin.write(json.dumps({"args": options, "brief": brief}))
        process.stdin.close()
    except (OSError, ValueError) as error:
        row.update(status="failed", exit_code=127, result=f"route run: {type(error).__name__} starting supervisor")
        save_run(row)
        print(json.dumps(row, sort_keys=True) if args.json else row["result"])
        return 127
    if args.bg:
        print(json.dumps(row, sort_keys=True) if args.json else decision_id)
        return 0
    return command_wait(argparse.Namespace(decision_id=decision_id, max=min(args.timeout + 15, 240), json=args.json))


def command_wait(args: argparse.Namespace) -> int:
    try:
        if not math.isfinite(args.max) or args.max < 0:
            raise ValueError("--max must be a nonnegative finite number")
        path = run_path(args.decision_id, ".json")
        deadline = time.monotonic() + min(args.max, 240)
        while True:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row["status"] not in ("queued", "running") or time.monotonic() >= deadline:
                break
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        if args.json:
            print(json.dumps(row, sort_keys=True))
        elif row.get("result"):
            print(row["result"])
        else:
            log = Path(row["log_path"])
            if log.is_file():
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 8192))
                    print(stream.read().decode("utf-8", errors="replace"))
            print(f"{row['status']}: {args.decision_id}; log: {log}")
        return 3 if row["status"] in ("queued", "running") else row.get("exit_code", 0 if row["status"] == "ok" else 2)
    except (OSError, ValueError, KeyError) as error:
        return setup_error("invalid_wait", str(error), args.json)
