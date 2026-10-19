#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

SERVICES = {
    "provider-response": ("agent-lb", "openai", "anthropic"),
    "pause-resume-account": ("agent-lb",),
    "inspect-failed-request": ("agent-lb",),
}

parser = argparse.ArgumentParser()
parser.add_argument("journey", choices=SERVICES)
args = parser.parse_args()
root = Path(__file__).resolve().parent
for service in SERVICES[args.journey]:
    result = subprocess.run(
        [sys.executable, str(root / service), "--json"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    try:
        identity = json.loads(result.stdout)
    except (ValueError, TypeError):
        print("Identity backend returned an invalid response; output withheld", file=sys.stderr)
        raise SystemExit(1)
    if result.returncode != 3 or identity.get("status") != "unavailable":
        print("Identity contract changed; proof needs an audited isolated driver", file=sys.stderr)
        raise SystemExit(3)
    if identity.get("identity_ref") is not None or identity.get("cleanup_cmd") is not None:
        print("Unavailable backend unexpectedly allocated state", file=sys.stderr)
        raise SystemExit(1)
    print(service + ": unavailable; no identity allocated")
print(args.journey + ": blocked; isolated seeded execution is unavailable")
raise SystemExit(3)
