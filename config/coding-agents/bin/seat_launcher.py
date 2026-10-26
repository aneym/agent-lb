#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path


def run(worktree, prompt, seat, model, label):
    env = dict(os.environ)
    env.update(
        ANTHROPIC_BASE_URL=env.get("CLAUDE_LB_BASE_URL") or "http://127.0.0.1:2455",
        ANTHROPIC_MODEL=model,
        ANTHROPIC_SMALL_FAST_MODEL=model,
        ANTHROPIC_DEFAULT_OPUS_MODEL=model,
        ANTHROPIC_DEFAULT_SONNET_MODEL=model,
        ANTHROPIC_DEFAULT_HAIKU_MODEL=model,
        CLAUDE_CODE_SUBAGENT_MODEL=model,
        ANTHROPIC_CUSTOM_HEADERS=f"x-agent-lb-seat: {seat}",
        API_TIMEOUT_MS="3000000",
    )
    env.pop("ANTHROPIC_API_KEY", None)
    started = time.time()
    print(f"{label}_MODEL={model} seat={seat} worktree={worktree}", flush=True)
    try:
        with Path(str(prompt) + ".json").open("w") as output, Path(str(prompt) + ".log").open("w") as log:
            command = shlex.split(env.get("AGENT_LB_SEAT_COMMAND", "")) or [
                str(Path.home() / ".local/bin/claude"),
                "-p",
                "--model",
                model,
                "--output-format",
                "json",
                "--no-session-persistence",
            ]
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                text=True,
                cwd=worktree,
                env=env,
                stdout=output,
                stderr=log,
                start_new_session=True,
            )
            assert proc.stdin is not None
            proc.stdin.write(prompt.read_text())
            proc.stdin.close()
            deadline = started + float(env.get("AGENT_LB_SEAT_DEADLINE", "3600"))
            while proc.poll() is None:
                if time.time() >= deadline:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    raise subprocess.TimeoutExpired(proc.args, deadline - started)
                print(f"{seat}: heartbeat {int(time.time() - started)}s", flush=True)
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    pass
        rc = proc.returncode
        data = json.loads(Path(str(prompt) + ".json").read_text())
        final = data.get("result", "")
        Path(str(prompt) + ".last").write_text(final)
        print(f"{label}_SESSION={data.get('session_id', '')}", flush=True)
        print(final, flush=True)
        if not final or "HANDOFF\n" not in final or "END HANDOFF" not in final:
            rc = rc or 65
        if data.get("is_error"):
            rc = rc or 1
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"{label}_ERROR={error}", flush=True)
        rc = 1
    print(f"{label}_RC={rc} elapsed={int(time.time() - started)}", flush=True)
    Path(str(prompt) + ".rc").write_text(str(rc))
    return rc


def main(seat, default_model, label):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "run", "wait"))
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--prompt-file", type=Path, required=True)
    args = parser.parse_args()
    prompt = args.prompt_file.resolve(strict=True)
    if args.action == "wait":
        deadline = time.monotonic() + 240
        receipt = Path(str(prompt) + ".rc")
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(2)
        if not receipt.exists():
            print(f"{label}_RUNNING")
            return 3
        rc = int(receipt.read_text())
        print(f"{label}_DONE rc={rc}")
        print(Path(str(prompt) + ".out").read_text()[-12000:])
        return rc
    if not args.worktree or not args.worktree.is_dir():
        parser.error("--worktree must name an existing directory")
    worktree = args.worktree.resolve()
    model = os.environ.get("AGENT_LB_OPENROUTER_MODEL") or default_model
    if args.action == "run":
        return run(worktree, prompt, seat, model, label)
    if any(Path(str(prompt) + suffix).exists() for suffix in (".out", ".rc", ".json")):
        parser.error("prompt already launched; use a fresh contract")
    with Path(str(prompt) + ".out").open("x") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "run",
                "--worktree",
                str(worktree),
                "--prompt-file",
                str(prompt),
            ],
            cwd=worktree,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(f"{label}_STARTED pid={process.pid} contract={prompt}")
    return 0
