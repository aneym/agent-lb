#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIT = "agent-lb-primary-tunnel.service"
KEY_NAME = "agent-lb-primary-tunnel"


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def replace_backed_up(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = path.read_bytes()
        if old == data:
            return False
        path.with_name(f"{path.name}.bak-{stamp()}").write_bytes(old)
    path.write_bytes(data)
    return True


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], check=check, capture_output=True, text=True)


def ensure_key(home):
    key = home / ".ssh" / KEY_NAME
    if not key.is_file():
        key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        comment = f"{KEY_NAME}@{subprocess.run(['hostname', '-s'], capture_output=True, text=True).stdout.strip()}"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)], check=True)
    return key


def authorized_line(key, remote_port):
    pub = key.with_name(f"{key.name}.pub").read_text().strip()
    return f'restrict,port-forwarding,permitopen="127.0.0.1:{remote_port}" {pub}'


def known_host_entry(home, host):
    found = subprocess.run(
        ["ssh-keygen", "-F", host, "-f", str(home / ".ssh/known_hosts")], capture_output=True, text=True
    )
    lines = [line for line in found.stdout.splitlines() if line and not line.startswith("#")]
    if not lines:
        raise SystemExit(f"{host} is not in ~/.ssh/known_hosts; connect once by hand to pin its host key")
    return "".join(f"{line}\n" for line in lines).encode()


def health(port):
    url = f"http://127.0.0.1:{port}/health"
    last = None
    for _ in range(20):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code
        except OSError as error:
            last = error
            time.sleep(1)
    raise SystemExit(f"{url} unreachable through the tunnel: {last}")


def main():
    parser = argparse.ArgumentParser(
        description="install a user ssh forward from this box to the agent-lb primary front"
    )
    parser.add_argument("--target", help="user@host of the primary box, e.g. jobs@100.124.242.6")
    parser.add_argument("--local-port", type=int, default=2464)
    parser.add_argument("--remote-port", type=int, default=2455)
    parser.add_argument("--print-authorized-key", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if sys.platform == "darwin":
        raise SystemExit("the primary tunnel unit is for Linux boxes; Studio uses its launchd tunnel")
    home = Path.home()
    unit_path = home / ".config/systemd/user" / UNIT
    if args.uninstall:
        systemctl("disable", "--now", UNIT, check=False)
        if unit_path.exists():
            unit_path.rename(unit_path.with_name(f"{UNIT}.bak-{stamp()}"))
        systemctl("daemon-reload")
        print(f"{UNIT} stopped and disabled")
        return
    key = ensure_key(home)
    if args.print_authorized_key:
        print(authorized_line(key, args.remote_port))
        return
    if not args.target:
        raise SystemExit("--target is required")
    host = args.target.rpartition("@")[2]
    config = home / ".config/agent-lb"
    replace_backed_up(config / "primary-tunnel.known_hosts", known_host_entry(home, host))
    env = (
        f'AGENT_LB_TUNNEL_TARGET="{args.target}"\n'
        f'AGENT_LB_TUNNEL_LOCAL_PORT="{args.local_port}"\n'
        f'AGENT_LB_TUNNEL_REMOTE_PORT="{args.remote_port}"\n'
    ).encode()
    env_changed = replace_backed_up(config / "primary-tunnel.env", env)
    unit_changed = replace_backed_up(unit_path, (ROOT / "deploy/systemd" / UNIT).read_bytes())
    systemctl("daemon-reload")
    systemctl("enable", UNIT)
    systemctl("restart" if env_changed or unit_changed else "start", UNIT)
    status = health(args.local_port)
    print(
        f"{UNIT} {systemctl('is-active', UNIT, check=False).stdout.strip()}; 127.0.0.1:{args.local_port}/health -> {status}"
    )
    if status != 200:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
