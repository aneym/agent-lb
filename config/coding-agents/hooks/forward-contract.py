#!/usr/bin/env python3

import argparse
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

RETRYABLE = (2,)

parser = argparse.ArgumentParser()
parser.add_argument("--key", required=True)
parser.add_argument("--role", type=Path)
parser.add_argument("--contract", type=Path)
parser.add_argument("--routing-json")
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--retry", action="store_true")
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
k = args.key
if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", k):
    parser.error("invalid job key")
header = f"seat-contract: {k}\n"
command = args.command
if command[:1] == ["--"]:
    command = command[1:]
command = [part for arg in command for part in (arg.split("=", 1) if arg.startswith("--prompt-file=") else [arg])]
if args.contract:
    printed = os.path.realpath(args.contract)
    command = [
        "@contract" if arg.endswith("contract.md") and os.path.realpath(arg) == printed else arg for arg in command
    ]
if not command or command.count("@contract") != 1:
    parser.error(
        f"command requires exactly one @contract prompt path, got {command.count('@contract')}: "
        "write --prompt-file @contract and let forward-contract.py substitute the contract it printed for this key"
    )
seat_run = Path(command[0]).name == "seat-run"
if seat_run:
    if "--name" not in command or command[command.index("--name") + 1] != k:
        parser.error("seat-run name must match the contract job key")
    if "--prompt-file" not in command or command[command.index("--prompt-file") + 1] != "@contract":
        parser.error("seat-run requires the owned contract as --prompt-file")
rehearsal = seat_run and "--dry-run" in command[: command.index("--") if "--" in command else len(command)]


def private(path, mode):
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        parser.error(f"{path} is not a directory you own")
    if stat.S_IMODE(info.st_mode) != mode:
        os.chmod(path, mode)


def contracts_root():
    root = Path(os.environ.get("TMPDIR") or "/tmp") / "seat-contracts"
    try:
        os.mkdir(root, 0o300)
    except FileExistsError:
        pass
    private(root, 0o300)
    return root


def owned(path):
    real = Path(os.path.realpath(path))
    if real.name != "contract.md" or real.parent.name != k or real.parent.parent.name != "seat-contracts":
        parser.error(f"--contract must be the exact path printed for {k}, ending in seat-contracts/{k}/contract.md")
    if not real.is_file():
        parser.error(f"{real} does not exist; launch with a new key instead")
    info = os.lstat(real.parent)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        parser.error(f"{real.parent} is not a private (0700) directory of yours")
    with real.open() as stream:
        if stream.readline() != header:
            parser.error(f"{real} does not carry the header for {k}")
    return real


def retry_once(directory):
    try:
        last = json.loads((directory / "launch.json").read_text())
    except (OSError, ValueError):
        last = {}
    if seat_run and not last.get("dry") and "rc" in last and last["rc"] not in RETRYABLE:
        parser.error(
            f"{last.get('name', k)} exited {last['rc']}, which is not a refused submission; "
            f"wait on it with seat-run --wait {last.get('name', k)} instead of retrying"
        )
    try:
        os.close(os.open(directory / "retried", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        parser.error(f"{k} was already retried once; report blocked")


if args.contract:
    contract = owned(args.contract)
    if args.retry:
        retry_once(contract.parent)
else:
    if args.retry:
        parser.error("retry requires the printed --contract path")
    role = ""
    if args.role:
        role = args.role.read_text()
        if "## Machine prompt\n" in role:
            role = "## Machine prompt\n" + role.split("## Machine prompt\n", 1)[1]
        elif role.startswith("---\n"):
            role = role.split("---\n", 2)[2]
        else:
            parser.error("role has no machine prompt")
    brief = sys.stdin.read()
    if not brief.strip():
        parser.error("empty contract brief")
    if args.routing_json:
        routing = json.loads(args.routing_json)
        if routing.get("degraded_from"):
            brief += (
                "\n## Live routing degradation\nResolved routing: "
                + args.routing_json
                + "\nRun on the resolved Sol model. Include degraded_from="
                + routing["degraded_from"]
                + " in HANDOFF deviations."
                + " The executing vendor is OpenAI, not Anthropic.\n"
            )
    directory = contracts_root() / k
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        parser.error(f"{k} already has a contract; launch with a new key, or retry with its printed --contract")
    private(directory, 0o700)
    contract = directory / "contract.md"
    with os.fdopen(os.open(contract, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as stream:
        stream.write(header + role + ("\n## Brief\n" if role else "") + brief)
name = k + ("-retry-" + secrets.token_hex(4) if args.retry else "")
if seat_run:
    command[command.index("--name") + 1] = name
command = [str(contract) if arg == "@contract" else arg for arg in command]
print(f"key={k} name={name} contract={contract}", flush=True)
if args.dry_run:
    print(shlex.join(command))
    sys.exit(0)
rc = subprocess.call(command)
launch = contract.parent / f".launch.{os.getpid()}"
launch.write_text(json.dumps({"name": name, "rc": rc, "dry": rehearsal, "at": time.time()}) + "\n")
os.replace(launch, contract.parent / "launch.json")
sys.exit(rc)
