#!/usr/bin/env python3
"""End-to-end prompt-cache eval for Claude Code through agent-lb.

Runs a real headless Claude Code session through `claude-lb-launch`: a few main
turns, one subagent with several turns, then more main turns. It then reads the
session's own transcripts and checks that every steady turn (not the first call
of a conversation) read its context from cache instead of rewriting it.

A turn "busts" when it writes more than 20k tokens and more than half its
context to cache: the cached prefix changed and the whole context was paid for
again. Pass = no busts and every conversation made at least 3 calls.

Run after any deploy that touches app/core/anthropic or app/modules/proxy:

    python3 scripts/claude_cache_eval.py            # sonnet, cheap
    python3 scripts/claude_cache_eval.py --model opus

Writes a JSON receipt to ~/.agent-lb/evals/cache-eval/receipts/ and exits 1 on
failure. Costs one short session (about 250k cached tokens, mostly reads).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
WORKDIR = HOME / ".agent-lb" / "evals" / "cache-eval"
LAUNCHER = HOME / ".local" / "bin" / "claude-lb-launch"
PROMPT = (
    "This is an automated prompt-cache probe. Do exactly this and nothing else. "
    "1) Run `echo main-1` with Bash. 2) Run `echo main-2` with Bash. "
    "3) Use the Agent tool (subagent_type general-purpose, description 'cache probe') with this prompt: "
    "'Run these Bash commands one at a time, one tool call each, waiting for each result: "
    "echo sub-1, echo sub-2, echo sub-3, echo sub-4, echo sub-5. Then reply DONE.' "
    "4) Run `echo main-3` with Bash. 5) Run `echo main-4` with Bash. Then reply FINISHED."
)


def calls(transcript: Path) -> list[dict]:
    seen: dict[str, dict] = {}
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") or {}
        usage = message.get("usage")
        if record.get("type") != "assistant" or not usage or message.get("model") == "<synthetic>":
            continue
        seen.setdefault(message.get("id"), usage)
    rows = []
    for index, usage in enumerate(seen.values()):
        write = usage.get("cache_creation_input_tokens") or 0
        read = usage.get("cache_read_input_tokens") or 0
        context = write + read + (usage.get("input_tokens") or 0)
        rows.append(
            {
                "first": index == 0,
                "context": context,
                "cache_write": write,
                "cache_read": read,
                "bust": index > 0 and write > 20_000 and write > context / 2,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    WORKDIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    proc = subprocess.run(
        [str(LAUNCHER), "-p", PROMPT, "--model", args.model, "--output-format", "json",
         "--permission-mode", "bypassPermissions"],
        cwd=WORKDIR, capture_output=True, text=True, timeout=args.timeout,
    )
    try:
        session_id = json.loads(proc.stdout.strip().splitlines()[-1])["session_id"]
    except (IndexError, KeyError, json.JSONDecodeError):
        print(f"FAIL: no session id from claude (exit {proc.returncode}): {proc.stderr[-400:]}", file=sys.stderr)
        return 1

    project = HOME / ".claude" / "projects" / str(WORKDIR).replace("/", "-").replace(".", "-")
    conversations = {"main": project / f"{session_id}.jsonl"}
    for sub in sorted((project / session_id / "subagents").glob("*.jsonl")):
        conversations[sub.stem] = sub

    report = {"session_id": session_id, "model": args.model, "seconds": round(time.time() - started), "conversations": {}}
    failures = []
    for name, path in conversations.items():
        rows = calls(path) if path.exists() else []
        busts = sum(row["bust"] for row in rows)
        report["conversations"][name] = {"calls": len(rows), "busts": busts, "rows": rows}
        if len(rows) < 3:
            failures.append(f"{name}: only {len(rows)} calls")
        if busts:
            failures.append(f"{name}: {busts} of {len(rows) - 1} steady turns rewrote their context")
    if len(conversations) < 2:
        failures.append("no subagent transcript found")
    report["pass"] = not failures
    report["failures"] = failures

    receipts = WORKDIR / "receipts"
    receipts.mkdir(exist_ok=True)
    receipt = receipts / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{args.model}.json"
    receipt.write_text(json.dumps(report, indent=1))
    for name, conv in report["conversations"].items():
        print(f"{name}: {conv['calls']} calls, {conv['busts']} busts")
    print(("PASS" if report["pass"] else "FAIL: " + "; ".join(failures)) + f"  receipt {receipt}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
