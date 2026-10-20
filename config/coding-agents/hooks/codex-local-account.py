#!/usr/bin/env python3

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ACCOUNT_COMMAND = re.compile(r"\bagent-test-account\b([^\n;`]*)(?:$|[\n;`])")
OWNER_FLAG = re.compile(r"--(lane|agent)(?:\s+|=)([A-Za-z0-9][A-Za-z0-9._-]*)")
OWNER_ENV = re.compile(r"AGENT_TEST_ACCOUNT_OWNER=(lane|agent):([A-Za-z0-9][A-Za-z0-9._-]*)")
RECRUITER = re.compile(r"agent-test-account\b[^\n;`]*--host(?:\s+|=)recruiter\b")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def roots():
    base = Path.home() / ".agent-rails"
    lanes = Path(os.environ.get("LANE_BULLETIN_HOME", str(base / "lanes"))).expanduser()
    return [("lane", lanes), ("agent", base / "agents")]


def owner(brief, env, cwd):
    commands = [OWNER_FLAG.findall(command) for command in ACCOUNT_COMMAND.findall(brief)]
    found = set(OWNER_ENV.findall(brief))
    for flags in commands:
        if len(flags) > 1:
            return None, "brief command names more than one account owner"
        found.update(flags)
    if len(found) > 1:
        return None, "brief names more than one account owner"
    selected = None
    if env.get("LANE_DIR"):
        name = Path(env["LANE_DIR"]).expanduser().name
        if not NAME.fullmatch(name):
            return None, "invalid launching lane owner"
        selected = ("lane", name)
    else:
        for kind, root in roots():
            try:
                parts = Path(cwd).resolve().relative_to(root.resolve()).parts
            except ValueError:
                continue
            if parts:
                selected = (kind, parts[0])
                break
    if not selected:
        return None, "no launching owner: export LANE_DIR or launch from a lane or agent directory"
    if found and found != {selected}:
        return None, "brief account owner differs from launching owner"
    return selected, None


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
    if "agent-test-account" not in brief:
        return None, "brief names no agent-test-account run"
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
            [helper, "--" + kind, name, *option, "--prepared-ttl-seconds", "7200", "--json"],
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
    parser.add_argument("--shell-env", action="store_true")
    parser.add_argument("--require-owner", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        brief = args.prompt_file.read_text() if args.prompt_file else ""
    except (OSError, UnicodeError) as exc:
        print(f"codex-local: cannot read brief: {exc}", file=sys.stderr)
        return 2
    if args.require_owner:
        if not brief.strip():
            print("codex-local: review requires a non-empty --prompt-file brief", file=sys.stderr)
            return 2
        selected, reason = owner(brief, dict(os.environ), os.getcwd())
        if not selected:
            print("codex-local: review refused: " + reason, file=sys.stderr)
            return 2
        kind, name = selected
        owner_path = next(root / name for owner_kind, root in roots() if owner_kind == kind).resolve()
        launch_path = Path(os.environ.get("LANE_DIR", str(owner_path))).expanduser().resolve()
        if not owner_path.is_dir() or launch_path != owner_path:
            print("codex-local: review refused: launching owner directory cannot be resolved", file=sys.stderr)
            return 2
    if args.validate_only:
        return 0
    prepared, reason = prepare(brief, dict(os.environ), os.getcwd())
    if prepared:
        kind, name = prepared.split(":", 1)
        print(
            f"codex-local: host prepared test account {prepared} for two hours; "
            f"inside the seat run agent-test-account --{kind} {name}",
            file=sys.stderr,
        )
        if args.shell_env:
            print("export AGENT_TEST_ACCOUNT_OWNER=" + shlex.quote(prepared))
            if kind == "lane":
                lane = host_env(kind, name, dict(os.environ))["LANE_DIR"]
                print("export LANE_DIR=" + shlex.quote(str(Path(lane).expanduser().resolve())))
            else:
                print("unset LANE_DIR")
        else:
            print(prepared)
    elif "agent-test-account" in brief:
        print("codex-local: test account not prepared: " + reason, file=sys.stderr)
        if args.require_owner:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
