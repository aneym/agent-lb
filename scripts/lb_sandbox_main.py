from __future__ import annotations

import os
import sys
from pathlib import Path

from scripts.lb_sandbox_cli import (
    EXEC_ENV_MARKER,
    build_parser,
    cmd_gc,
    cmd_restart,
    cmd_stop,
    in_live_venv,
    isolated_env,
    isolated_python,
    run_marker,
)
from scripts.lb_sandbox_commands import (
    cmd_aux,
    cmd_client_env,
    cmd_fault,
    cmd_scan,
    cmd_status,
)
from scripts.lb_sandbox_edge import boot_app
from scripts.lb_sandbox_guards import (
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNHEALTHY,
    LIVE_VENV,
    Refused,
    Unhealthy,
    bootstrap_script,
)
from scripts.lb_sandbox_lifecycle import cmd_start
from scripts.lb_sandbox_runtime import cmd_serve
from scripts.lb_sandbox_system import (
    emit,
)

COMMANDS = {
    "start": cmd_start,
    "status": cmd_status,
    "client-env": cmd_client_env,
    "fault": cmd_fault,
    "restart": cmd_restart,
    "scan": cmd_scan,
    "stop": cmd_stop,
    "gc": cmd_gc,
}


def main(argv: list[str]) -> int:
    if argv[:1] == ["_serve"] and len(argv) >= 2:
        try:
            return cmd_serve(argv[1], argv[2:])
        except Refused as exc:
            print(f"lb-sandbox _serve refused: {exc}", file=sys.stderr)
            return EXIT_REFUSED
    if argv[:1] == ["_boot"] and len(argv) >= 2:
        try:
            boot_app(Path(argv[1]), argv[2:])
            return EXIT_OK
        except Refused as exc:
            print(f"lb-sandbox _boot refused: {exc}", file=sys.stderr)
            return EXIT_REFUSED
    if argv[:1] == ["_aux"] and len(argv) == 2:
        try:
            return cmd_aux(argv[1])
        except Refused as exc:
            print(f"lb-sandbox _aux refused: {exc}", file=sys.stderr)
            return EXIT_REFUSED
    args = build_parser().parse_args(argv)
    marker = run_marker(args)
    if marker is not None:
        os.environ["LB_SANDBOX_ROOT"] = marker
    if not os.environ.get("LB_SANDBOX_NO_REEXEC"):
        python = LIVE_VENV / "bin" / "python"
        reexec = (not in_live_venv() and python.exists()) or (marker is not None and marker != EXEC_ENV_MARKER)
        if reexec:
            target = str(python) if python.exists() and not in_live_venv() else sys.executable
            os.execve(target, isolated_python(target, bootstrap_script(), argv), isolated_env(dict(os.environ)))
    try:
        return COMMANDS[args.command](args)
    except Refused as exc:
        return emit({"refused": str(exc)}, EXIT_REFUSED)
    except Unhealthy as exc:
        return emit({"error": str(exc)}, EXIT_UNHEALTHY)
