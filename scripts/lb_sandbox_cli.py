from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from scripts.lb_sandbox_filesystem import append_in_root, check_own_volume, confined
from scripts.lb_sandbox_guards import (
    EDGES,
    EXIT_ASSERT,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNHEALTHY,
    LIVE_VENV,
    Refused,
    Unhealthy,
    check_launchctl,
    check_run_id,
    now_iso,
    root_for,
)
from scripts.lb_sandbox_lifecycle import teardown
from scripts.lb_sandbox_runtime import DEFAULT_REPO
from scripts.lb_sandbox_system import (
    emit,
    load_meta,
    pid_alive,
    sandbox_roots,
    stamped_meta,
)


def cmd_stop(args) -> int:
    check_launchctl()
    run_id = check_run_id(args.run_id)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    result = teardown(run_id, Path(args.logs_to) if args.logs_to else None)
    result["run_id"] = run_id
    if result.get("refused"):
        return emit(result, EXIT_REFUSED)
    safe_logs = result.get("logs", {})
    safe = (
        safe_logs.get("scan_found") is False
        and safe_logs.get("scan_complete") is True
        and not safe_logs.get("secret_load_error")
        and not safe_logs.get("copy_error")
        and not safe_logs.get("export_found")
        and result.get("custody", {}).get("key_unlinked") is not False
    )
    return emit(result, EXIT_OK if result["clean"] and safe else EXIT_ASSERT)


def cmd_gc(args) -> int:
    check_launchctl()
    stopped, kept, skipped = [], [], []
    now = time.time()
    for root in sandbox_roots():
        run_id = root.name
        meta = stamped_meta(root, run_id)
        created = meta.get("created_epoch")
        if not meta or isinstance(created, bool) or not isinstance(created, (int, float)):
            skipped.append({"run_id": run_id, "reason": "no sandbox.json stamped with this run id"})
            continue
        if now - float(created) > args.older_than and not pid_alive(meta.get("caller_pid")):
            result = teardown(run_id, None)
            stopped.append(run_id) if result["clean"] else kept.append({"run_id": run_id, "teardown": result})
    return emit({"stopped": stopped, "not_clean": kept, "skipped": skipped}, EXIT_OK if not kept else EXIT_ASSERT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lb-sandbox",
        description="lb-sandbox: a throwaway agent-lb instance on Studio that mirrors the live account pool.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--json", action="store_true", help="print one JSON object (always on)")
        return p

    p = command("start", "create, boot and mirror a sandbox")
    p.add_argument("--run-id", required=True)
    p.add_argument("--from-live", action="store_true", help="runtime code from the installed live runtime")
    p.add_argument(
        "--from-ref",
        metavar="REF",
        help="runtime code from this agent-lb git ref (a clean tree of that commit); accounts still mirror live",
    )
    p.add_argument("--repo", default=str(DEFAULT_REPO), help="the agent-lb repo --from-ref resolves REF in")
    p.add_argument("--port", type=int)
    p.add_argument("--min-accounts", type=int, default=2)
    p.add_argument("--transport", default="http")
    p.add_argument("--logs-to", help="on failure, copy scanned logs here before teardown")
    p = command("status", "health, accounts, gate and edge counters")
    p.add_argument("--run-id", required=True)
    p = command("client-env", "env for a client pointed only at the sandbox")
    p.add_argument("--run-id", required=True)
    p.add_argument("--vendor", choices=("claude", "codex"), required=True)
    p = command("fault", "arm or clear one edge fault")
    p.add_argument("--run-id", required=True)
    p.add_argument("--edge", choices=tuple(EDGES), required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--arm")
    group.add_argument("--clear", action="store_true")
    p = command("restart", "blue/green restart of the sandbox through lb-restart --sandbox")
    p.add_argument("--run-id", required=True)
    p.add_argument("--reason", required=True)
    p = command("scan", "search files for the live federation token and mirrored tokens")
    p.add_argument("--run-id", required=True)
    p.add_argument("--newer-than", help="only files modified after this file")
    p.add_argument("--exclude", action="append", default=[], help="skip this path (repeatable)")
    p.add_argument("--processes", action="store_true", help="also scan the exec env of every process of the run")
    p.add_argument("paths", nargs="*")
    p = command("stop", "tear the sandbox down and verify")
    p.add_argument("--run-id", required=True)
    p.add_argument("--logs-to")
    p = command("gc", "stop abandoned sandboxes")
    p.add_argument("--older-than", type=float, default=3600)
    return parser


EXEC_ENV_MARKER = os.environ.get("LB_SANDBOX_ROOT")


def run_marker(args) -> str | None:

    run_id = getattr(args, "run_id", None)
    try:
        return str(root_for(run_id)) if run_id else None
    except Refused:
        return None


def in_live_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == LIVE_VENV.resolve()
    except OSError:
        return False


def isolated_env(env: dict[str, str]) -> dict[str, str]:

    return {k: v for k, v in env.items() if not k.startswith(("PYTHON", "DYLD_"))}


def isolated_python(python: str, script: Path, argv: list[str]) -> list[str]:

    return [python, "-I", str(script), *argv]


def cmd_restart(args) -> int:
    check_launchctl()
    root, _ = load_meta(args.run_id)

    check_own_volume(root)
    lb_restart = Path(__file__).resolve().parent / "lb-restart"
    if not lb_restart.is_file():
        raise Unhealthy(f"{lb_restart} missing")
    log_path = root / "logs" / "lb-restart.log"

    argv = isolated_python(
        sys.executable,
        lb_restart,
        [
            "--sandbox",
            str(root / "lb-restart.json"),
            "--skip-exchange-wait",
            "--skip-exit-timeout-gate",
            "--reason",
            args.reason,
        ],
    )
    started = time.monotonic()

    with confined(lambda: append_in_root(root, "logs/lb-restart.log")) as log:
        log.write(f"==== {now_iso()} lb-sandbox restart: {' '.join(argv[1:])}\n")
        log.flush()

        env = {**isolated_env(dict(os.environ)), "LB_SANDBOX_ROOT": str(root)}
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(root), env=env
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            log.flush()
            sys.stderr.write(line)
        code = proc.wait()
    out = {"lb_restart_exit": code, "log": str(log_path), "seconds": round(time.monotonic() - started, 1)}
    return emit(out, EXIT_OK if code == 0 else EXIT_UNHEALTHY)
