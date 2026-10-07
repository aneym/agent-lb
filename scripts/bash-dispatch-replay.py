#!/usr/bin/env python3
"""Replay check for config/coding-agents/hooks/bash-dispatch.py against the live Bash hooks (2026-10-07).

For each payload it runs every Bash PreToolUse hook from settings.json on its own, the way Claude Code does today,
and then the dispatcher over the same hooks, and compares the decisions: blocked or not (with every blocker's
message), a JSON deny, and the rewritten command (the dispatcher's must be one of the rewrites the hooks returned,
since Claude Code applies one of parallel rewrites). Payloads come from recent Claude Code transcripts (--sample) and
from --extra (one JSON payload per line: each guard's block cases). Payloads that would wake the display
(computer-use words) are left out: display-wake.sh acts on the screen.

Prints counts and timing only, never a command (transcripts can hold secrets). Exit 1 on any mismatch.
  bash-dispatch-replay.py [--sample N] [--extra FILE] [--settings PATH] [--days D]
"""
import argparse
import importlib.util
import json
import os
import random
import re
import resource
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DISPATCH = Path(__file__).resolve().parents[1] / "config/coding-agents/hooks/bash-dispatch.py"
WAKE = re.compile(r"computer[_-]?use|cua-driver|screencapture", re.I)


def bash_hooks(settings):
    return [hook for group in settings.get("hooks", {}).get("PreToolUse", []) if group.get("matcher") == "Bash"
            for hook in group.get("hooks", []) if hook.get("type", "command") == "command"]


def transcripts(days, limit):
    cutoff = time.time() - days * 86400
    rows = []
    for path in Path.home().joinpath(".claude/projects").rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
            for line in path.open(errors="replace"):
                if '"Bash"' not in line:
                    continue
                entry = json.loads(line)
                content = (entry.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                        command = (block.get("input") or {}).get("command")
                        if isinstance(command, str):
                            rows.append((command, entry.get("cwd") or str(Path.home())))
        except (OSError, ValueError):
            continue
    unique = list({command: (command, cwd) for command, cwd in rows}.values())
    random.Random(7).shuffle(unique)
    return [{"tool_name": "Bash", "hook_event_name": "PreToolUse", "cwd": cwd,
             "tool_input": {"command": command, "description": "replay"}} for command, cwd in unique[:limit]]


def run_one(hook, raw):
    started = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        done = subprocess.run(["/bin/sh", "-c", hook["command"]], input=raw, capture_output=True, text=True,
                              timeout=float(hook.get("timeout") or 60))
        return done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        return None


def expected(hooks, raw, pool):
    results = list(pool.map(lambda hook: run_one(hook, raw), hooks))
    blocked = [r[2] for r in results if r and r[0] == 2]
    rewrites, deny = set(), False
    for r in results:
        if r and r[0] == 0 and r[1].strip():
            try:
                data = json.loads(r[1])
            except ValueError:
                continue
            specific = data.get("hookSpecificOutput") or {}
            deny |= specific.get("permissionDecision") == "deny"
            if isinstance(specific.get("updatedInput"), dict):
                rewrites.add(json.dumps(specific["updatedInput"], sort_keys=True))
    return blocked, deny, rewrites


def actual(raw, env):
    done = subprocess.run(["/usr/bin/python3", str(DISPATCH)], input=raw, capture_output=True, text=True, env=env,
                          timeout=120)
    rewrite, deny = None, False
    if done.returncode == 0 and done.stdout.strip():
        data = json.loads(done.stdout)
        specific = data.get("hookSpecificOutput") or {}
        deny = specific.get("permissionDecision") == "deny"
        if isinstance(specific.get("updatedInput"), dict):
            rewrite = json.dumps(specific["updatedInput"], sort_keys=True)
    return done.returncode, done.stderr, deny, rewrite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=400)
    parser.add_argument("--days", type=float, default=2)
    parser.add_argument("--extra")
    parser.add_argument("--settings", default=str(Path.home() / ".claude/settings.json"))
    args = parser.parse_args()
    hooks = bash_hooks(json.loads(Path(args.settings).read_text()))
    payloads = transcripts(args.days, args.sample)
    if args.extra:
        payloads += [json.loads(line) for line in Path(args.extra).read_text().splitlines() if line.strip()]
    payloads = [p for p in payloads if not WAKE.search(p["tool_input"]["command"])]
    spec = importlib.util.spec_from_file_location("bash_dispatch", DISPATCH)
    dispatch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dispatch)
    with tempfile.TemporaryDirectory() as tmp:
        listed, bare = Path(tmp, "bash-hooks.json"), Path(tmp, "settings.json")
        listed.write_text(json.dumps(hooks))
        bare.write_text("{}")
        env = {**os.environ, "BASH_DISPATCH_LIST": str(listed), "BASH_DISPATCH_SETTINGS": str(bare)}
        stats = dict(payloads=len(payloads), hooks=len(hooks), mismatch=0, blocked=0, rewritten=0, multi_rewrite=0,
                     hooks_run=0)
        timing = dict(before_wall=0.0, after_wall=0.0, before_cpu=0.0, after_cpu=0.0)
        with ThreadPoolExecutor(max_workers=len(hooks)) as pool:
            for index, payload in enumerate(payloads):
                raw = json.dumps(payload)
                cpu0, wall0 = resource.getrusage(resource.RUSAGE_CHILDREN), time.monotonic()
                blocked, deny, rewrites = expected(hooks, raw, pool)
                cpu1, wall1 = resource.getrusage(resource.RUSAGE_CHILDREN), time.monotonic()
                code, err, got_deny, rewrite = actual(raw, env)
                cpu2, wall2 = resource.getrusage(resource.RUSAGE_CHILDREN), time.monotonic()
                timing["before_wall"] += wall1 - wall0
                timing["after_wall"] += wall2 - wall1
                timing["before_cpu"] += (cpu1.ru_utime + cpu1.ru_stime) - (cpu0.ru_utime + cpu0.ru_stime)
                timing["after_cpu"] += (cpu2.ru_utime + cpu2.ru_stime) - (cpu1.ru_utime + cpu1.ru_stime)
                stats["hooks_run"] += sum(dispatch.needed(h["command"], raw, payload) for h in hooks)
                stats["blocked"] += bool(blocked)
                stats["rewritten"] += bool(rewrites)
                stats["multi_rewrite"] += len(rewrites) > 1
                if blocked:
                    ok = code == 2 and all(message.strip() in err for message in blocked)
                elif deny:
                    ok = code == 0 and got_deny
                else:
                    ok = code != 2 and not got_deny and (rewrite in rewrites if rewrites else rewrite is None)
                if not ok:
                    stats["mismatch"] += 1
                    print(f"MISMATCH payload #{index}: expected blocked={bool(blocked)} deny={deny} "
                          f"rewrites={len(rewrites)}; got exit {code} deny={got_deny} rewrite={rewrite is not None}")
    n = max(1, stats["payloads"])
    print(json.dumps({**stats, "hooks_run_per_call": round(stats["hooks_run"] / n, 2),
                      **{k: round(v / n, 3) for k, v in timing.items()}}, indent=1))
    return 1 if stats["mismatch"] else 0


if __name__ == "__main__":
    sys.exit(main())
