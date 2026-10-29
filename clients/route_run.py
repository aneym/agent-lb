from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from route_brief_paths import caller_host_paths  # pyrefly: ignore [missing-import]
from route_pace_state import reservation_request  # pyrefly: ignore [missing-import]
from route_resolve_one import append_decision  # pyrefly: ignore [missing-import]
from route_run_checkout import (  # pyrefly: ignore [missing-import]
    REMOTE_CLEANUP_GRACE_S,
    cleanup_remote,
    git_value,
    prepare_remote,
    remote_command,
    repo_root,
    validate_worktree,
)
from route_run_jobs import launch_run, run_path, save_run  # pyrefly: ignore [missing-import]
from route_target import local_environment, resolve_target, t3_settings  # pyrefly: ignore [missing-import]
from route_versioned_rules import EXIT_OK, EXIT_WAIT, load_table, now_iso, setup_error  # pyrefly: ignore [missing-import]


def provider_command(row: dict, cwd: str, write: bool, help_text: str) -> list[str]:
    provider = row["target"]["providerInstanceId"]
    if provider == "codex":
        return [
            "codex",
            "exec",
            "--model",
            row["model"],
            "-c",
            "model_reasoning_effort=" + json.dumps(row["effort"]),
            "--sandbox",
            "danger-full-access" if write else "read-only",
            "--cd=" + cwd,
            "--skip-git-repo-check",
            "--json",
            "-",
        ]
    if provider != "claudeAgent":
        raise ValueError(f"unsupported headless provider: {provider}")
    return [
        "claude" if row["environment"] == "studio" else "claude-box-launch",
        "-p",
        "--model",
        row["model"],
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--permission-mode",
        "bypassPermissions",
        *(["--effort", row["effort"]] if "--effort" in help_text else []),
        *([] if write else ["--disallowedTools", "Edit,Write,NotebookEdit"]),
    ]


def cli_help(provider: str, host: str | None) -> str:
    argv = ["claude", "--help"] if provider == "claudeAgent" else ["codex", "exec", "--help"]
    if host:
        argv = ["ssh", "-o", "BatchMode=yes", host, "bash -lc " + shlex.quote(shlex.join(argv))]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError("selected provider CLI help failed on the execution host")
    return result.stdout


def final_message(path: Path, provider: str) -> str:
    final = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if provider == "codex":
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                final = item.get("text", "")
        elif event.get("type") == "result":
            final = event.get("result") or "\n".join(event.get("errors") or [])
        elif event.get("type") == "assistant":
            final = "\n".join(
                block["text"] for block in event.get("message", {}).get("content", []) if block.get("type") == "text"
            )
    return final


def release_lease(row: dict, outcome: str) -> None:
    code, _ = reservation_request("POST", f"/{row['lease_id']}/release", {"outcome": outcome})
    if code != EXIT_OK:
        print("route run: lease release unavailable; lease will expire", file=sys.stderr)


def execute_run(args: argparse.Namespace, row: dict, cwd: Path, brief: str) -> int:
    started = time.monotonic()
    log_path = run_path(row["decision_id"], ".log")
    process = None
    timed_out = False
    exit_code = 127
    result = ""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        local = local_environment(t3_settings(load_table())["environments"])
        host = None
        execution_cwd = str(cwd)
        if row["environment"] != local and not args.write:
            try:
                if not args.on:
                    named = caller_host_paths(brief, repo_root(cwd))
                    if named:
                        raise ValueError("brief names caller-host paths " + ", ".join(named[:3]))
                row.update(prepare_remote(cwd, row))
                execution_cwd = row["execution_cwd"]
                host = row["ssh_host"]
            except ValueError as error:
                row["placement_reason"] += f"; {error}"
                if args.on:
                    row["placement_reason"] += "; pinned remote execution refused"
                    raise
                row["placement_reason"] += "; fallback to caller environment"
                row["environment"] = local
                row["ssh_host"] = t3_settings(load_table())["environments"][local]["ssh_host"]
                row["target"]["environmentId"] = t3_settings(load_table())["environments"][local]["environmentId"]
        if args.write:
            row["environment"] = local
        row.update(status="running", log_path=str(log_path))
        save_run(row)
        if not host:
            try:
                row["execution_sha"] = git_value(cwd, "rev-parse", "HEAD")
            except ValueError:
                row["execution_sha"] = None
        provider = row["target"]["providerInstanceId"]
        argv = provider_command(row, execution_cwd, args.write, cli_help(provider, host))
        if host:
            argv = remote_command(argv, row, max(0.01, args.run_deadline - time.monotonic()))
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        env.pop("CLAUDECODE", None)
        args.timeout = max(0.01, min(args.timeout, args.run_deadline - time.monotonic()))
        row.update(execution_transport="ssh" if host else "local", execution_cwd=execution_cwd)
        save_run(row)
        with log_path.open("w", encoding="utf-8") as transcript:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=transcript,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            release_lease(row, "ok")
            try:
                process.communicate(brief, timeout=args.timeout + (REMOTE_CLEANUP_GRACE_S if host else 0))
                exit_code = process.returncode
                timed_out = exit_code == 124 and host is not None
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                exit_code, timed_out = 124, True
        result = final_message(log_path, provider)
    except ValueError as error:
        result = f"route run: {error}"
        print(result, file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as error:
        result = f"route run: {type(error).__name__}; see execution configuration"
        print(result, file=sys.stderr)
    finally:
        if process is None:
            release_lease(row, "cancelled")
        try:
            cleanup_remote(row)
            if row.get("remote_scratch"):
                row["checkout_cleaned"] = True
        except (OSError, ValueError, subprocess.SubprocessError):
            row["checkout_cleaned"] = False
            result += "\nroute run: remote decision scratch checkout cleanup failed"
            if exit_code == 0:
                exit_code = 127
    exit_code = exit_code if exit_code >= 0 else 128 - exit_code
    seconds = round(time.monotonic() - started, 3)
    append_decision(
        {
            "v": 1,
            "kind": "route.run.outcome",
            "decision_id": row["decision_id"],
            "ts": now_iso(),
            "exit_code": exit_code,
            "seconds": seconds,
            "timed_out": timed_out,
            "log_path": str(log_path),
            "environment": row["environment"],
            "placement_reason": row["placement_reason"],
            "execution_sha": row.get("execution_sha"),
            "execution_cwd": row.get("execution_cwd"),
            "checkout_cleaned": row.get("checkout_cleaned"),
        }
    )
    output = {
        **row,
        "status": "ok" if exit_code == 0 else "timeout" if timed_out else "failed",
        **{key: row[key] for key in ("decision_id", "seat", "model", "environment", "placement_reason")},
        "exit_code": exit_code,
        "seconds": seconds,
        "result": result,
        "log_path": str(log_path),
    }
    save_run(output)
    print(json.dumps(output, sort_keys=True) if args.json else result)
    return exit_code if exit_code >= 0 else 128 - exit_code


def command_run(args: argparse.Namespace) -> int:
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("--timeout must be a positive finite number")
        cwd = (args.cwd or Path.cwd()).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError("--cwd must be a directory")
        if args.write:
            if args.cwd is None:
                raise ValueError("--write requires --cwd")
            validate_worktree(cwd)
            if args.on and args.on != local_environment(t3_settings(load_table())["environments"]):
                raise ValueError("remote writes unsupported; --write stays on the caller environment")
        brief = getattr(args, "run_brief", None)
        if brief is None:
            brief = sys.stdin.read() if args.brief_stdin else args.brief.read_text(encoding="utf-8")
        if not brief.strip():
            raise ValueError("brief must not be empty")
    except (OSError, ValueError) as error:
        return setup_error("invalid_run", str(error), args.json)
    if not getattr(args, "detached", False):
        return launch_run(args, cwd, brief)
    target_args = argparse.Namespace(
        task_class=args.task_class,
        job=args.job,
        caller=args.caller,
        author_vendor=args.author_vendor,
        effort=args.effort,
        write=args.write,
        here=None,
        on=args.on,
        mac=False,
        dry_run=False,
        json=args.json,
        decision_id=getattr(args, "decision_id", None),
    )
    code, row = resolve_target(target_args)
    if code != EXIT_OK or row.get("status") != "ok":
        queued = code in (EXIT_OK, EXIT_WAIT) and row.get("status") == "queued"
        row["decision_id"] = args.decision_id
        row["log_path"] = str(run_path(args.decision_id, ".log"))
        if not queued:
            row.update(status="failed", exit_code=code if code != EXIT_OK else 2)
            row["result"] = row.get("message") or row.get("reason") or "route run: target resolution failed"
        save_run(row)
        print(json.dumps(row, sort_keys=True))
        return EXIT_WAIT if queued else row["exit_code"]
    return execute_run(args, row, cwd, brief)
