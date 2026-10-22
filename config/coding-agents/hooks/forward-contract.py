#!/usr/bin/env python3

import argparse
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
from pathlib import Path

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
command = args.command
if command[:1] == ["--"]:
    command = command[1:]
if not command or command.count("@contract") != 1:
    parser.error("command requires exactly one @contract prompt path")
if args.contract:
    contract = args.contract.resolve()
    if not contract.name.startswith(k + ".contract.") or not contract.is_file():
        parser.error("contract path does not belong to this job key")
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
    filename = subprocess.check_output(
        ["mktemp", str(Path(os.environ.get("TMPDIR", "/tmp")) / (k + ".contract.XXXXXX"))], text=True
    ).strip()
    contract = Path(filename).resolve()
    with contract.open("w") as stream:
        stream.write(role + ("\n## Brief\n" if role else "") + brief)
name = k + ("-retry-" + secrets.token_hex(4) if args.retry else "")
if Path(command[0]).name == "seat-run":
    if "--name" not in command or command[command.index("--name") + 1] != k:
        parser.error("seat-run name must match the contract job key")
    command[command.index("--name") + 1] = name
    if "--prompt-file" not in command or command[command.index("--prompt-file") + 1] != "@contract":
        parser.error("seat-run requires the owned contract as --prompt-file")
command = [str(contract) if arg == "@contract" else arg for arg in command]
print(f"key={k} name={name} contract={contract}", flush=True)
if args.dry_run:
    print(shlex.join(command))
else:
    sys.exit(subprocess.call(command))
