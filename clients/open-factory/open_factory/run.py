"""Execute one host-routed brief, with bounded stand-ins and durable receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .common import resolve_bin, utc_now
from .decide import ledger_path, run_route

READ_ONLY = {"explore", "research", "review", "verify", "plan"}
LIMIT = re.compile(r"429|rate.?limit|usage limit|quota|hit your limit", re.IGNORECASE)


def maker(pool: str, model: str) -> str:
    if pool.startswith("anthropic-"):
        return "anthropic"
    if pool.startswith("openai-"):
        return "openai"
    if pool == "devin":
        return "cognition"
    if pool.startswith("cursor"):
        if model.startswith("grok"):
            return "xai"
        if model.startswith("claude-"):
            return "anthropic"
        return "cursor"
    return pool


def append_row(row: dict[str, Any]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def pick(task_class: str, author_vendor: str | None, skipped: list[str]) -> dict[str, Any] | None:
    args = ["pick", task_class, "--json"]
    if author_vendor:
        args.extend(["--author-vendor", author_vendor])
    for rung in skipped:
        args.extend(["--skip", rung])
    try:
        code, body, _ = run_route(*args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return body if code == 0 and isinstance(body, dict) and body.get("seat") else None


def command(seat: dict[str, Any], task_class: str, cwd: Path, brief: str, out: Path) -> list[str]:
    pool, model = seat.get("pool") or "", seat.get("model")
    readonly = task_class in READ_ONLY
    if pool.startswith("anthropic-"):
        name = "claude-lb-launch"
        args = ["-p", "--model", model, "--output-format", "json"]
        args.extend(["--permission-mode", "plan"] if readonly else ["--dangerously-skip-permissions"])
        args.append(brief)
    elif pool.startswith("openai-"):
        name = "codex"
        args = [
            "exec",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{seat.get("effort") or "medium"}"',
            "-s",
            "read-only" if readonly else "workspace-write",
            "-C",
            str(cwd),
            "-o",
            str(out) + ".last",
            brief,
        ]
    elif pool.startswith("cursor") or pool == "devin":
        name = "seat"
        prompt = out.with_suffix(".prompt")
        prompt.write_text(brief, encoding="utf-8")
        args = [
            "run",
            "--vendor",
            "cursor" if pool.startswith("cursor") else "devin",
            "--model",
            model,
            "--class",
            task_class,
            "--cwd",
            str(cwd),
            "--prompt-file",
            str(prompt),
        ]
        if readonly:
            args.extend(["--mode", "ask"])
    else:
        raise OSError(f"no adapter for pool {pool or '(none)'}")
    binary = resolve_bin(name)
    if not binary:
        raise OSError(f"{name} not found")
    return [binary, *args]


def attempt(
    seat: dict[str, Any], task_class: str, cwd: Path, brief: str, out: Path, timeout: float
) -> tuple[str, int, float, str]:
    started = time.monotonic()
    env = dict(os.environ, AGENT_LB_INTENT=task_class)
    try:
        with out.open("w", encoding="utf-8") as handle:
            proc = subprocess.run(
                command(seat, task_class, cwd, brief, out),
                cwd=cwd,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        code = proc.returncode
        text = out.read_text(encoding="utf-8", errors="replace")
        outcome = (
            "ok"
            if code == 0
            else (
                "limit"
                if (seat.get("pool", "").startswith("cursor") or seat.get("pool") == "devin")
                and code == 3
                or LIMIT.search(text)
                else "infra"
                if code in {75, 124}
                else "fail"
            )
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        code, outcome = (124 if isinstance(error, subprocess.TimeoutExpired) else 75), "infra"
        with out.open("a", encoding="utf-8") as handle:
            handle.write("\nattempt timed out\n" if isinstance(error, subprocess.TimeoutExpired) else f"\n{error}\n")
        text = out.read_text(encoding="utf-8", errors="replace")
    first_line = next((line for line in text.splitlines() if line.strip()), f"exit {code}")
    return outcome, code, round(time.monotonic() - started, 3), first_line


def run(
    brief: str, task_class: str, *, intended: str | None, author_vendor: str | None, cwd: Path, timeout: float
) -> dict[str, Any]:
    started = time.monotonic()
    first = pick(task_class, author_vendor, [])
    current = first
    reason = None
    intention = {"seat": first.get("seat") if first else None, "model": first.get("model") if first else None}
    if intended:
        intention = {"seat": None, "model": intended}
        try:
            _, menu, _ = run_route("menu", "--class", task_class, "--json")
        except (OSError, subprocess.TimeoutExpired):
            menu = None
        seats = (menu or {}).get("classes", {}).get(task_class, {}).get("seats", [])
        match = next((seat for seat in seats if intended in (seat.get("model"), seat.get("alias"))), None)
        if match:
            current = match
            intention = {"seat": match["seat"], "model": match.get("model")}
        else:
            reason = f"intended {intended} is not on the {task_class} menu"
    decision_id = uuid.uuid4().hex[:16]
    append_row(
        {
            "ts": utc_now(),
            "event": "of_decision",
            "decision_id": decision_id,
            "session_id": os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID"),
            "cwd": str(cwd),
            "task_class": task_class,
            "task_sha256": hashlib.sha256(brief.encode()).hexdigest(),
            "task_head": brief[:160],
            "decider": "ladder" if first and "rung" in first else "route",
            "intended": intention,
            "seat": current.get("seat") if current else None,
            "model": current.get("model") if current else None,
            "pool": current.get("pool") if current else None,
            "pick": current.get("seat") if current else None,
            "total_ms": round((time.monotonic() - started) * 1000),
        }
    )
    directory = Path.home() / ".agent-lb" / "of" / "runs" / decision_id
    directory.mkdir(parents=True, exist_ok=True)
    attempts, skipped, seen = [], [], set()
    fallbacks = (first or {}).get("fallbacks", [])
    ran, out, exit_code = None, None, 2
    while current and len(attempts) < 3:
        identity = (current.get("seat"), current.get("model"))
        if identity in seen:
            break
        seen.add(identity)
        number = len(attempts) + 1
        out = directory / f"attempt-{number}.txt"
        outcome, code, wall, line = attempt(current, task_class, cwd, brief, out, timeout)
        row = {
            "ts": utc_now(),
            "event": "of_outcome",
            "decision_id": decision_id,
            "outcome": outcome,
            "host": socket.gethostname(),
            "attempt": number,
            "seat": current["seat"],
            "model": current.get("model"),
            "exit": code,
            "wall_s": wall,
        }
        append_row(row)
        attempts.append(
            {
                "seat": current["seat"],
                "model": current.get("model"),
                "pool": current.get("pool"),
                "maker": maker(current.get("pool") or "", current.get("model") or ""),
                "outcome": outcome,
                "exit": code,
                "wall_s": wall,
            }
        )
        if outcome == "ok":
            ran, exit_code = {"seat": current["seat"], "model": current.get("model")}, 0
            break
        if reason is None:
            reason = f"{current['seat']} {outcome}: {line}"
        if outcome == "fail":
            exit_code = 1
            break
        if first and "rung" in first:
            rung = first["rung"] if number == 1 else current["rung"]
            skipped.append(str(rung))
            current = pick(task_class, author_vendor, skipped)
        else:
            current = next((seat for seat in fallbacks if (seat.get("seat"), seat.get("model")) not in seen), None)
    return {
        "decision_id": decision_id,
        "class": task_class,
        "intended": intention,
        "ran": ran,
        "standing_in": ran is not None and ran != intention,
        "reason": reason,
        "attempts": attempts,
        "out": str(out) if out else None,
        "exit": exit_code,
    }
