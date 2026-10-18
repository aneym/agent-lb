#!/usr/bin/env python3

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

OWNER_FLAG = re.compile(r"agent-test-account\b[^\n;`]*?--(lane|agent)(?:\s+|=)([A-Za-z0-9][A-Za-z0-9._-]*)")
OWNER_ENV = re.compile(r"AGENT_TEST_ACCOUNT_OWNER=(lane|agent):([A-Za-z0-9][A-Za-z0-9._-]*)")
RECRUITER = re.compile(r"agent-test-account\b[^\n;`]*--host(?:\s+|=)recruiter\b")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def roots():
    base = Path.home() / ".agent-rails"
    lanes = Path(os.environ.get("LANE_BULLETIN_HOME", str(base / "lanes"))).expanduser()
    return [("lane", lanes), ("agent", base / "agents")]


def owner(brief, env, cwd):
    if "agent-test-account" not in brief:
        return None, "brief names no agent-test-account run"
    found = set(OWNER_FLAG.findall(brief)) | set(OWNER_ENV.findall(brief))
    if len(found) > 1:
        return None, "brief names more than one account owner: " + ", ".join(sorted(k + ":" + n for k, n in found))
    if found:
        return next(iter(found)), None
    selected = env.get("AGENT_TEST_ACCOUNT_OWNER", "")
    kind, _, name = selected.partition(":")
    if kind in ("lane", "agent") and NAME.fullmatch(name):
        return (kind, name), None
    if env.get("LANE_DIR"):
        return ("lane", Path(env["LANE_DIR"]).expanduser().name), None
    for kind, root in roots():
        try:
            parts = Path(cwd).resolve().relative_to(root.resolve()).parts
        except ValueError:
            continue
        if parts:
            return (kind, parts[0]), None
    return None, "no owner: name --lane/--agent in the brief, export LANE_DIR, or launch from a lane or agent directory"


def host_env(kind, name, env):
    child = {k: v for k, v in env.items() if k not in ("CODEX_SANDBOX", "AGENT_TEST_ACCOUNT_PREPARED")}
    child["AGENT_TEST_ACCOUNT_OWNER"] = kind + ":" + name
    if kind == "lane":
        lane = env.get("LANE_DIR", "")
        if not lane or Path(lane).expanduser().name != name:
            lane = str(roots()[0][1] / name)
        child["LANE_DIR"] = lane
    else:
        child.pop("LANE_DIR", None)
    return child


def prepare(brief, env, cwd):
    selected, reason = owner(brief, env, cwd)
    if not selected:
        return None, reason
    kind, name = selected
    helper = shutil.which("agent-test-account", path=env.get("PATH"))
    if not helper:
        return None, "agent-test-account is not on PATH"
    option = ["--host", "recruiter"] if RECRUITER.search(brief) else ["--link"]
    try:
        result = subprocess.run(
            [helper, "--" + kind, name, *option, "--json"],
            env=host_env(kind, name, env),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, f"host preflight for {kind}:{name} timed out"
    if result.returncode:
        return None, f"host preflight for {kind}:{name} exited {result.returncode}"
    return kind + ":" + name, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", type=Path)
    args = parser.parse_args()
    brief = args.prompt_file.read_text() if args.prompt_file else ""
    prepared, reason = prepare(brief, dict(os.environ), os.getcwd())
    if prepared:
        kind, name = prepared.split(":", 1)
        print(
            f"codex-local: host prepared test account {prepared} for five minutes; "
            f"inside the seat run agent-test-account --{kind} {name}",
            file=sys.stderr,
        )
        print(prepared)
    elif "agent-test-account" in brief:
        print("codex-local: test account not prepared: " + reason, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
