#!/usr/bin/env python3
"""End-to-end prompt-cache eval for Claude Code through agent-lb.

Runs a real headless Claude Code session through `claude-lb-launch`: a few main
turns, one subagent with several turns, then more main turns. It then reads the
session's own transcripts and checks that every steady turn (not the first call
of a conversation) read its context from cache instead of rewriting it.

A turn "busts" when it writes more than 20k tokens and more than half its
context to cache: the cached prefix changed and the whole context was paid for
again. Pass = no busts and every conversation made at least 3 calls.

`--arm sdk` drives the same launcher through `@anthropic-ai/claude-agent-sdk`
(one process for T1–T4, then a resumed process for T5). A T3 bust, or T3 with
no usage records, fails that arm; idle and resume are reported and do not fail
the run. The sdk arm reads transcripts from `--config-dir` (default ~/.claude)
and ignores an inherited CLAUDE_CONFIG_DIR; the cli arm still honors that env.

Run after any deploy that touches app/core/anthropic or app/modules/proxy:

    python3 scripts/claude_cache_eval.py            # sonnet, cheap
    python3 scripts/claude_cache_eval.py --model opus

Writes a JSON receipt to ~/.agent-lb/evals/cache-eval/receipts/ and exits 1 on
failure. Costs one short session (about 250k cached tokens, mostly reads).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
WORKDIR = HOME / ".agent-lb" / "evals" / "cache-eval"
LAUNCHER = HOME / ".local" / "bin" / "claude-lb-launch"
SDK_DRIVER = Path(__file__).resolve().parent / "claude_cache_eval_sdk.mjs"
BUST_WRITE = 20_000
TURNS = ("T1", "T2", "T3", "T4", "T5")
PHASE_TURNS = {"live": ("T1", "T2", "T3"), "idle": ("T4",), "resume": ("T5",)}
PHASE_BUST_TURN = {"live": "T3", "idle": "T4", "resume": "T5"}
PROMPT = (
    "This is an automated prompt-cache probe. Do exactly this and nothing else. "
    "1) Run `echo main-1` with Bash. 2) Run `echo main-2` with Bash. "
    "3) Use the Agent tool (subagent_type general-purpose, description 'cache probe') with this prompt: "
    "'Run these Bash commands one at a time, one tool call each, waiting for each result: "
    "echo sub-1, echo sub-2, echo sub-3, echo sub-4, echo sub-5. Then reply DONE.' "
    "4) Run `echo main-3` with Bash. 5) Run `echo main-4` with Bash. Then reply FINISHED."
)
_SECRET = re.compile(r"sk-[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    return _SECRET.sub("<redacted>", text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--arm", choices=("cli", "sdk"), default="cli")
    parser.add_argument("--launcher", type=Path, default=LAUNCHER)
    parser.add_argument("--idle-seconds", type=int, default=360)
    parser.add_argument("--sdk-dir", default=None)
    parser.add_argument("--config-dir", type=Path, default=HOME / ".claude")
    return parser.parse_args(argv)


def sdk_dir_path(args: argparse.Namespace) -> Path:
    raw = args.sdk_dir or os.environ.get("CLAUDE_AGENT_SDK_DIR") or str(HOME / ".agent-lb" / "evals" / "sdk")
    return Path(raw).expanduser()


def claude_config_dir() -> Path:
    raw = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser() if raw else HOME / ".claude"


def transcript_root(args: argparse.Namespace) -> Path:
    if args.arm == "sdk":
        return Path(args.config_dir).expanduser()
    return claude_config_dir()


def project_dir(workdir: Path, root: Path | None = None) -> Path:
    base = claude_config_dir() if root is None else root
    return base / "projects" / str(workdir).replace("/", "-").replace(".", "-")


def timestamp_ms(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def context_of(usage: dict) -> tuple[int, int, int]:
    write = int(usage.get("cache_creation_input_tokens") or 0)
    read = int(usage.get("cache_read_input_tokens") or 0)
    incoming = int(usage.get("input_tokens") or 0)
    return write, read, write + read + incoming


def is_bust(usage: dict, *, first: bool) -> bool:
    write, _read, context = context_of(usage)
    return (not first) and write > BUST_WRITE and write > context / 2


def sum_tiers(usages: Iterable[dict]) -> dict:
    cache_read = cache_write = ephemeral_5m = ephemeral_1h = 0
    for usage in usages:
        write, read, _context = context_of(usage)
        creation = usage.get("cache_creation") or {}
        cache_read += read
        cache_write += write
        ephemeral_5m += int(creation.get("ephemeral_5m_input_tokens") or 0)
        ephemeral_1h += int(creation.get("ephemeral_1h_input_tokens") or 0)
    return {
        "cache_read": cache_read,
        "cache_write": cache_write,
        "ephemeral_5m_input_tokens": ephemeral_5m,
        "ephemeral_1h_input_tokens": ephemeral_1h,
    }


def write_split(usages: Iterable[dict]) -> dict:
    totals = sum_tiers(usages)
    return {
        "ephemeral_5m_input_tokens": totals["ephemeral_5m_input_tokens"],
        "ephemeral_1h_input_tokens": totals["ephemeral_1h_input_tokens"],
    }


def assistant_usages(lines: Iterable[str]) -> list[dict]:
    seen: dict[str, dict] = {}
    order: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") or {}
        usage = message.get("usage")
        if record.get("type") != "assistant" or not usage or message.get("model") == "<synthetic>":
            continue
        key = message.get("id")
        if key in seen:
            continue
        seen[key] = {"usage": usage, "timestamp_ms": timestamp_ms(record.get("timestamp"))}
        order.append(key)
    return [seen[key] for key in order]


def calls(transcript: Path) -> tuple[list[dict], dict]:
    items = assistant_usages(transcript.read_text(errors="replace").splitlines())
    rows = []
    usages = []
    for index, item in enumerate(items):
        usage = item["usage"]
        usages.append(usage)
        write, read, context = context_of(usage)
        rows.append(
            {
                "first": index == 0,
                "context": context,
                "cache_write": write,
                "cache_read": read,
                "bust": is_bust(usage, first=index == 0),
            }
        )
    return rows, write_split(usages)


def assign_turns(lines: Iterable[str], marks: list[dict]) -> dict[str, list[dict]]:
    assigned: dict[str, list[dict]] = {mark["turn"]: [] for mark in marks}
    for item in assistant_usages(lines):
        stamp = item["timestamp_ms"]
        if stamp is None:
            continue
        for mark in marks:
            start, end = mark.get("startedAt"), mark.get("endedAt")
            if start is None or end is None:
                continue
            if start <= stamp <= end:
                assigned[mark["turn"]].append(item["usage"])
                break
    return assigned


def sdk_verdict(turn_usages: dict[str, list[dict]]) -> dict:
    ordered: list[tuple[str, dict]] = []
    for turn in TURNS:
        ordered.extend((turn, usage) for usage in turn_usages.get(turn, []))
    bust = dict.fromkeys(TURNS, False)
    for index, (turn, usage) in enumerate(ordered):
        if is_bust(usage, first=index == 0):
            bust[turn] = True
    phases = {}
    for name, turns in PHASE_TURNS.items():
        usages = [usage for turn in turns for usage in turn_usages.get(turn, [])]
        phases[name] = {**sum_tiers(usages), "bust": bust[PHASE_BUST_TURN[name]]}
    failures: list[str] = []
    if not turn_usages.get("T3"):
        failures.append("T3 had no records")
    elif bust["T3"]:
        failures.append("T3: steady turn rewrote its context")
    return {
        "phases": phases,
        "pass": not failures,
        "failures": failures,
    }


def write_receipt(report: dict, model: str, arm: str) -> Path:
    receipts = WORKDIR / "receipts"
    receipts.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"{stamp}-{model}.json" if arm == "cli" else f"{stamp}-{model}-sdk.json"
    receipt = receipts / name
    receipt.write_text(json.dumps(report, indent=1))
    return receipt


def run_cli(args: argparse.Namespace, started: float) -> int:
    proc = subprocess.run(
        [str(args.launcher), "-p", PROMPT, "--model", args.model, "--output-format", "json",
         "--permission-mode", "bypassPermissions"],
        cwd=WORKDIR, capture_output=True, text=True, timeout=args.timeout,
    )
    try:
        session_id = json.loads(proc.stdout.strip().splitlines()[-1])["session_id"]
    except (IndexError, KeyError, json.JSONDecodeError):
        print(f"FAIL: no session id from claude (exit {proc.returncode}): {redact(proc.stderr[-400:])}",
              file=sys.stderr)
        return 1

    project = project_dir(WORKDIR)
    conversations = {"main": project / f"{session_id}.jsonl"}
    for sub in sorted((project / session_id / "subagents").glob("*.jsonl")):
        conversations[sub.stem] = sub

    report: dict = {
        "arm": "cli",
        "session_id": session_id,
        "model": args.model,
        "seconds": round(time.time() - started),
        "conversations": {},
    }
    failures = []
    total_5m = 0
    total_1h = 0
    for name, path in conversations.items():
        rows, split = calls(path) if path.exists() else ([], write_split([]))
        busts = sum(row["bust"] for row in rows)
        total_5m += split["ephemeral_5m_input_tokens"]
        total_1h += split["ephemeral_1h_input_tokens"]
        report["conversations"][name] = {
            "calls": len(rows), "busts": busts, "rows": rows, "write_split": split,
        }
        if len(rows) < 3:
            failures.append(f"{name}: only {len(rows)} calls")
        if busts:
            failures.append(f"{name}: {busts} of {len(rows) - 1} steady turns rewrote their context")
    if len(conversations) < 2:
        failures.append("no subagent transcript found")
    report["write_split"] = {
        "ephemeral_5m_input_tokens": total_5m,
        "ephemeral_1h_input_tokens": total_1h,
    }
    report["pass"] = not failures
    report["failures"] = failures
    receipt = write_receipt(report, args.model, "cli")
    for name, conv in report["conversations"].items():
        print(f"{name}: {conv['calls']} calls, {conv['busts']} busts")
    print(("PASS" if report["pass"] else "FAIL: " + "; ".join(failures)) + f"  receipt {receipt}")
    return 0 if report["pass"] else 1


def sdk_stdout(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "session" in payload and "marks" in payload:
            return payload
    raise ValueError("no sdk result")


def run_sdk(args: argparse.Namespace, started: float) -> int:
    directory = sdk_dir_path(args)
    package = directory / "node_modules" / "@anthropic-ai" / "claude-agent-sdk"
    if not package.is_dir():
        print(f"npm i --prefix {directory} @anthropic-ai/claude-agent-sdk", file=sys.stderr)
        return 2
    proc = subprocess.run(
        ["node", str(SDK_DRIVER),
         "--sdk-dir", str(directory),
         "--model", args.model,
         "--idle-seconds", str(args.idle_seconds),
         "--launcher", str(args.launcher),
         "--workdir", str(WORKDIR)],
        cwd=WORKDIR, capture_output=True, text=True,
        timeout=args.timeout + args.idle_seconds,
    )
    try:
        payload = sdk_stdout(proc.stdout)
    except ValueError:
        print(f"FAIL: no sdk result (exit {proc.returncode}): {redact(proc.stderr[-400:])}", file=sys.stderr)
        return 1
    session_id = payload.get("session")
    if proc.returncode or not session_id:
        print(f"FAIL: sdk driver exit {proc.returncode}: {redact(proc.stderr[-400:])}", file=sys.stderr)
        return 1

    project = project_dir(WORKDIR, transcript_root(args))
    paths = [project / f"{session_id}.jsonl"]
    resumed = payload.get("resumedSession")
    if resumed and resumed != session_id:
        paths.append(project / f"{resumed}.jsonl")
    lines: list[str] = []
    for path in paths:
        if path.exists():
            lines.extend(path.read_text(errors="replace").splitlines())
    if lines:
        verdict = sdk_verdict(assign_turns(lines, payload.get("marks") or []))
    else:
        verdict = {
            "phases": {name: {**sum_tiers([]), "bust": False} for name in PHASE_TURNS},
            "pass": False,
            "failures": ["no transcript"],
        }
    report = {
        "arm": "sdk",
        "session_id": session_id,
        "resumed_session": resumed,
        "model": args.model,
        "seconds": round(time.time() - started),
        "phases": verdict["phases"],
        "pass": verdict["pass"],
        "failures": verdict["failures"],
    }
    receipt = write_receipt(report, args.model, "sdk")
    for name, phase in report["phases"].items():
        print(f"{name}: read {phase['cache_read']} write {phase['cache_write']} bust {phase['bust']}")
    failures = report["failures"]
    print(("PASS" if report["pass"] else "FAIL: " + "; ".join(failures)) + f"  receipt {receipt}")
    return 0 if report["pass"] else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    WORKDIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    if args.arm == "sdk":
        return run_sdk(args, started)
    return run_cli(args, started)


if __name__ == "__main__":
    sys.exit(main())
