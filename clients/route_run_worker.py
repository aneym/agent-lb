from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from route_run import command_run  # pyrefly: ignore [missing-import]
from route_run_jobs import run_path, save_run  # pyrefly: ignore [missing-import]


def main() -> int:
    request = json.load(sys.stdin)
    args = argparse.Namespace(**request["args"])
    args.cwd = Path(args.cwd)
    args.run_brief = request["brief"]
    deadline = time.monotonic() + args.timeout
    args.run_deadline = deadline
    try:
        while True:
            args.timeout = max(0.01, deadline - time.monotonic())
            code = command_run(args)
            row = json.loads(run_path(args.decision_id, ".json").read_text())
            if row["status"] != "queued":
                return code
            if time.monotonic() >= deadline:
                row.update(status="timeout", exit_code=124, result="route run: admission timeout")
                save_run(row)
                return 124
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    except BaseException as error:
        row = json.loads(run_path(args.decision_id, ".json").read_text())
        row.update(status="failed", exit_code=127, result=f"route run: {type(error).__name__}; see supervisor log")
        save_run(row)
        raise


if __name__ == "__main__":
    sys.exit(main())
