#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIT = "agent-lb-proxy.service"
OLD_UNIT = "factory-lb-proxy.service"
KEYS = ("PRIMARY", "HOP_PORT", "PROXY_PORT", "OUTER_UPSTREAMS")


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


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            parsed = shlex.split(value)
            values[name.strip()] = parsed[0] if parsed else ""
    return values


def migrated_env(home, drop):
    source = home / ".config/factory/lb-proxy.env"
    if not source.is_file():
        raise SystemExit(f"no env at {source}; write {home / '.config/agent-lb/lb-proxy.env'} by hand")
    old = read_env(source)
    missing = [key for key in KEYS if f"FACTORY_LB_{key}" not in old]
    if missing:
        raise SystemExit(f"{source} lacks FACTORY_LB_{', FACTORY_LB_'.join(missing)}")
    values = {key: old[f"FACTORY_LB_{key}"] for key in KEYS}
    upstreams = [item for item in values["OUTER_UPSTREAMS"].split() if item not in drop]
    if not upstreams:
        raise SystemExit("dropping those upstreams leaves none")
    values["OUTER_UPSTREAMS"] = " ".join(upstreams)
    return "".join(f'AGENT_LB_{key}="{values[key]}"\n' for key in KEYS)


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], check=check, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description="install the box-local agent-lb failover proxy from this checkout")
    parser.add_argument(
        "--drop-upstream", action="append", default=[], help="outer upstream to leave out when migrating the old env"
    )
    args = parser.parse_args()
    if sys.platform == "darwin":
        raise SystemExit("the failover proxy runs on Linux boxes only")
    home = Path.home()
    if not (home / ".local/bin/caddy").is_file():
        raise SystemExit(f"caddy missing at {home / '.local/bin/caddy'}")
    config = home / ".config/agent-lb"
    env = config / "lb-proxy.env"
    if not env.is_file():
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text(migrated_env(home, set(args.drop_upstream)))
        env.chmod(0o600)
    caddyfile = config / "lb-proxy/Caddyfile"
    changed = replace_backed_up(caddyfile, (ROOT / "deploy/lb-proxy/Caddyfile").read_bytes())
    validate = subprocess.run(
        [
            "/usr/bin/env",
            "bash",
            "-c",
            f"set -a; . {shlex.quote(str(env))}; exec {shlex.quote(str(home / '.local/bin/caddy'))} validate --config {shlex.quote(str(caddyfile))} --adapter caddyfile",
        ],
        capture_output=True,
        text=True,
    )
    if validate.returncode:
        raise SystemExit(f"caddy validate failed: {validate.stderr.strip()[-400:]}")
    unit_changed = replace_backed_up(
        home / ".config/systemd/user" / UNIT, (ROOT / "deploy/systemd" / UNIT).read_bytes()
    )
    systemctl("daemon-reload")
    if systemctl("cat", OLD_UNIT, check=False).returncode == 0:
        systemctl("disable", "--now", OLD_UNIT)
        print(f"{OLD_UNIT} stopped and disabled")
    systemctl("enable", UNIT)
    systemctl("restart" if changed or unit_changed else "start", UNIT)
    state = systemctl("is-active", UNIT, check=False).stdout.strip()
    print(f"{UNIT} {state} with {caddyfile}")
    if state != "active":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
