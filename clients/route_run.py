from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from route_pace_state import reservation_request  # pyrefly: ignore [missing-import]
from route_resolve_one import append_decision  # pyrefly: ignore [missing-import]
from route_target import local_environment, resolve_target, t3_settings  # pyrefly: ignore [missing-import]
from route_versioned_rules import EXIT_OK, EXIT_WAIT, load_table, now_iso, setup_error  # pyrefly: ignore [missing-import]


def git_value(cwd: Path, *argv: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *argv], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise ValueError("cwd must be a git checkout")
    return result.stdout.strip()


def validate_worktree(cwd: Path) -> None:
    root = Path(git_value(cwd, "rev-parse", "--show-toplevel")).resolve()
    git_dir = Path(git_value(cwd, "rev-parse", "--absolute-git-dir")).resolve()
    common = Path(git_value(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if root != cwd or git_dir == common:
        raise ValueError("--write requires --cwd at the root of a linked git worktree, not a main checkout")


def remote_checkout(cwd: Path, host: str, environment: str) -> str | None:
    try:
        origin = git_value(cwd, "remote", "get-url", "origin")
        name = origin.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            return None
        base = "/Volumes/StudioExt/repos/" if environment == "studio" else "$HOME/repos/"
        path = base + name
        probe = f'git -C "{path}" rev-parse --show-toplevel 2>/dev/null'
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, probe],
            capture_output=True,
            text=True,
            timeout=12,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


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
            "workspace-write" if write else "read-only",
            "--cd=" + cwd,
            "--skip-git-repo-check",
            "--json",
            "-",
        ]
    if provider != "claudeAgent":
        raise ValueError(f"unsupported headless provider: {provider}")
    return [
        "claude",
        "-p",
        "--model",
        row["model"],
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--permission-mode",
        "acceptEdits" if write else "plan",
        *(["--effort", row["effort"]] if "--effort" in help_text else []),
        *([] if write else ["--disallowedTools", "Edit,Write,NotebookEdit,ExitPlanMode"]),
    ]


def cli_help(provider: str, host: str | None) -> str:
    argv = ["claude", "--help"] if provider == "claudeAgent" else ["codex", "exec", "--help"]
    if host:
        argv = ["ssh", "-o", "BatchMode=yes", host, "bash -lc " + shlex.quote(shlex.join(argv))]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError("selected provider CLI help failed on the execution host")
    return result.stdout


def remote_command(argv: list[str], cwd: str, host: str, timeout: float) -> list[str]:
    script = """import os,signal,subprocess,sys
brief=sys.stdin.buffer.read()
os.environ.pop("OPENAI_API_KEY",None)
os.environ.pop("CLAUDECODE",None)
p=subprocess.Popen(sys.argv[3:],cwd=sys.argv[1],stdin=subprocess.PIPE,start_new_session=True)
try:
 p.communicate(brief,timeout=float(sys.argv[2]))
except subprocess.TimeoutExpired:
 os.killpg(p.pid,signal.SIGKILL)
 p.communicate()
 sys.exit(124)
sys.exit(p.returncode if p.returncode >= 0 else 128-p.returncode)
"""
    command = shlex.join(["python3", "-c", script, cwd, str(timeout), *argv])
    return ["ssh", "-o", "BatchMode=yes", host, "bash -lc " + shlex.quote(command)]


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
    log_path = Path.home() / ".agent-lb/logs/route-run" / (row["decision_id"] + ".log")
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
            execution_cwd = remote_checkout(cwd, row["ssh_host"], row["environment"])
            if execution_cwd:
                host = row["ssh_host"]
            elif args.on:
                row["placement_reason"] += "; pinned host has no matching checkout; execution refused"
                raise ValueError("pinned environment has no matching repo checkout")
            else:
                row["placement_reason"] += "; no matching remote checkout; running locally"
                row["environment"] = local
                execution_cwd = str(cwd)
        if args.write:
            row["environment"] = local
        provider = row["target"]["providerInstanceId"]
        argv = provider_command(row, execution_cwd, args.write, cli_help(provider, host))
        if host:
            argv = remote_command(argv, execution_cwd, host, args.timeout)
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        env.pop("CLAUDECODE", None)
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
                process.communicate(brief, timeout=args.timeout + (5 if host else 0))
                exit_code = process.returncode
                timed_out = exit_code == 124 and host is not None
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                exit_code, timed_out = 124, True
        result = final_message(log_path, provider)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result = f"route run: {type(error).__name__}; see execution configuration"
        print(result, file=sys.stderr)
    finally:
        if process is None:
            release_lease(row, "cancelled")
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
        }
    )
    output = {
        "status": "ok" if exit_code == 0 else "timeout" if timed_out else "failed",
        **{key: row[key] for key in ("decision_id", "seat", "model", "environment", "placement_reason")},
        "exit_code": exit_code,
        "seconds": seconds,
        "result": result,
        "log_path": str(log_path),
    }
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
        brief = sys.stdin.read() if args.brief_stdin else args.brief.read_text(encoding="utf-8")
        if not brief.strip():
            raise ValueError("brief must not be empty")
    except (OSError, ValueError) as error:
        return setup_error("invalid_run", str(error), args.json)
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
    )
    code, row = resolve_target(target_args)
    if code != EXIT_OK or row.get("status") != "ok":
        print(json.dumps(row, sort_keys=True))
        return EXIT_WAIT if row.get("status") == "queued" else code
    return execute_run(args, row, cwd, brief)
