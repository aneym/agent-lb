#!/usr/bin/env python3
"""End-to-end eval for Claude Code sessions and subagents on GPT models via agent-lb.

Runs real headless Claude Code sessions through `claude-lb-launch` (the same MITM
path interactive `cc` sessions use) and checks that each one finishes a tool-using
task instead of hanging:

- `sol` / `luna`: a session on sol-latest-low / luna-latest-low (resolved by the LB
  to the newest served gpt-*-sol / gpt-*-luna) runs Bash, then Read, then replies
  with a token only the file contains.
- `subagent`: a Claude session delegates the same task to a subagent whose
  definition pins model sol-latest-low.
- `ccgpt`: the same task in the launcher's fail-closed GPT mode
  (CLAUDE_LB_CODEX_MODE=1), which sends every turn through /v1/ccgpt/messages.

A case passes when the session exits 0 within its timeout, the final reply holds
the token, the transcript that ran on GPT shows completed Bash and Read tool
calls, and the LB request log shows the session's GPT calls prompt-cached: every
call after a conversation's first reads cache (so no Codex account switch mid
conversation). Run after any deploy that touches the ccgpt bridge:

    python3 scripts/ccgpt_e2e_eval.py                 # all cases
    python3 scripts/ccgpt_e2e_eval.py --case sol

Writes a JSON receipt to ~/.agent-lb/evals/ccgpt-e2e/receipts/ and exits 1 on
failure.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
WORKDIR = HOME / ".agent-lb" / "evals" / "ccgpt-e2e"
LAUNCHER = HOME / ".local" / "bin" / "claude-lb-launch"
PROBE_AGENT = "ccgpt-probe"
TASK = (
    "This is an automated probe. Do exactly this and nothing else. "
    "1) Run `echo ccgpt-bash-ok` with the Bash tool. "
    "2) Read the file token.txt in the current directory with the Read tool. "
    "3) Reply with exactly: DONE <the token from the file>."
)
CASES = {
    "sol": {"model": "sol-latest-low", "prompt": TASK},
    "luna": {"model": "luna-latest-low", "prompt": TASK},
    "subagent": {
        "model": "sonnet",
        "prompt": (
            f"Use the Agent tool with subagent_type {PROBE_AGENT} and this exact prompt: '{TASK}' "
            "Then reply with exactly the subagent's final line and nothing else."
        ),
    },
    "ccgpt": {"model": "sol-latest", "prompt": TASK, "env": {"CLAUDE_LB_CODEX_MODE": "1"}},
}


def tool_calls(transcript: Path) -> tuple[set[str], set[str], set[str]]:
    """Return (models, tool names called, tool names whose result came back)."""
    models: set[str] = set()
    called: dict[str, str] = {}
    answered: set[str] = set()
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") or {}
        content = message.get("content")
        if record.get("type") == "assistant" and message.get("model") not in (None, "<synthetic>"):
            models.add(message["model"])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                called[block.get("id")] = block.get("name")
            elif block.get("type") == "tool_result" and not block.get("is_error"):
                answered.add(block.get("tool_use_id"))
    return models, set(called.values()), {called[i] for i in answered if i in called}


def run_case(name: str, timeout: int) -> dict:
    case = CASES[name]
    token = secrets.token_hex(6)
    (WORKDIR / "token.txt").write_text(f"{token}\n")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [
                str(LAUNCHER),
                "-p",
                case["prompt"],
                "--model",
                case["model"],
                "--output-format",
                "json",
                "--permission-mode",
                "bypassPermissions",
            ],
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **case.get("env", {})},
        )
    except subprocess.TimeoutExpired:
        return {"case": name, "pass": False, "seconds": timeout, "failures": [f"hung: no exit within {timeout}s"]}
    seconds = round(time.monotonic() - started, 1)
    result: dict = {"case": name, "model": case["model"], "seconds": seconds, "exit": proc.returncode}
    failures: list[str] = []
    try:
        final = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        final = {}
        failures.append(f"no JSON result (exit {proc.returncode}): {proc.stderr[-300:]}")
    reply = str(final.get("result", ""))
    result.update({"session_id": final.get("session_id"), "reply": reply[-200:], "turns": final.get("num_turns")})
    if proc.returncode != 0 or final.get("is_error"):
        failures.append(f"session failed (exit {proc.returncode}, is_error={final.get('is_error')})")
    if token not in reply:
        failures.append("final reply lacks the file token")

    project = HOME / ".claude" / "projects" / str(WORKDIR).replace("/", "-").replace(".", "-")
    session_id = final.get("session_id")
    transcripts = [project / f"{session_id}.jsonl"] if session_id else []
    if name == "subagent" and session_id:
        transcripts = sorted((project / session_id / "subagents").glob("*.jsonl"))
        if not transcripts:
            failures.append("no subagent transcript")
    for path in transcripts:
        if not path.exists():
            failures.append(f"missing transcript {path.name}")
            continue
        models, called, answered = tool_calls(path)
        result.setdefault("transcripts", []).append(
            {"file": path.name, "models": sorted(models), "called": sorted(called), "answered": sorted(answered)}
        )
        if not any(model.startswith("gpt-") for model in models):
            failures.append(f"{path.name}: no GPT-served turn (models {sorted(models)})")
        missing = {"Bash", "Read"} - answered
        if missing:
            failures.append(f"{path.name}: tool calls without results: {sorted(missing)}")
    if session_id:
        failures += cache_failures(session_id, result)
    result["pass"] = not failures
    result["failures"] = failures
    return result


def cache_failures(session_id: str, result: dict) -> list[str]:
    """A conversation's GPT calls stay on one Codex account and read its prompt cache.

    A session holds one conversation per case, plus the subagent's in `subagent`.
    Any call after the first with no cache read means the prefix changed or the
    call moved accounts; either way the bridge lost the cache.
    """
    query = Path(__file__).with_name("request_log_query.py")
    proc = subprocess.run(
        [sys.executable, str(query), "--session", session_id, "--json"], capture_output=True, text=True, timeout=60
    )
    try:
        rows = json.loads(proc.stdout)["requests"]
    except (json.JSONDecodeError, KeyError):
        return [f"request log unreadable for session {session_id}: {proc.stderr[-200:]}"]
    gpt = sorted(
        (row for row in rows if str(row.get("model", "")).startswith("gpt-")), key=lambda row: row["requested_at"]
    )
    result["gpt_calls"] = [{"account": row.get("account"), "cache_read": row.get("cache_read")} for row in gpt]
    if len(gpt) < 2:
        return [f"request log shows {len(gpt)} GPT call(s); need 2+ to judge caching"]
    uncached = [row for row in gpt[1:] if not row.get("cache_read")]
    # A subagent's first call starts a second conversation, so it may miss once.
    allowed = 1 if result["case"] == "subagent" else 0
    if len(uncached) > allowed:
        accounts = sorted({str(row.get("account")) for row in gpt})
        return [f"{len(uncached)} GPT call(s) after the first read no cache (accounts {accounts})"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", choices=sorted(CASES), action="append")
    parser.add_argument("--timeout", type=int, default=300, help="seconds per case before it counts as hung")
    args = parser.parse_args()

    agents = WORKDIR / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{PROBE_AGENT}.md").write_text(
        f"---\nname: {PROBE_AGENT}\ndescription: ccgpt e2e probe subagent\nmodel: sol-latest-low\n"
        "tools: Bash, Read\n---\n\nFollow the task exactly.\n"
    )
    results = [run_case(name, args.timeout) for name in args.case or list(CASES)]
    receipts = WORKDIR / "receipts"
    receipts.mkdir(exist_ok=True)
    receipt = receipts / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    ok = all(result["pass"] for result in results)
    receipt.write_text(json.dumps({"pass": ok, "results": results}, indent=1))
    for result in results:
        verdict = "PASS" if result["pass"] else "FAIL: " + "; ".join(result["failures"])
        print(f"{result['case']}: {verdict} ({result['seconds']}s)")
    print(("PASS" if ok else "FAIL") + f"  receipt {receipt}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
