from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from policy_documents import read_text  # type: ignore[missing-import]
from policy_hosts import policy_box_hosts  # type: ignore[missing-import]
from policy_layout import POLICY_DIR, ROUTE_FILES, ROUTING_TABLE  # type: ignore[missing-import]


def desired_routing_table(source_path: Path, live_path: Path) -> str:
    table = json.loads(source_path.read_text())
    live_text = read_text(live_path)
    if live_text:
        try:
            live = json.loads(live_text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"error: invalid JSON in {live_path}: {exc}") from exc
        if isinstance(live, dict) and "overrides" in live:
            table["overrides"] = [
                entry
                for entry in live["overrides"]
                if isinstance(entry, dict) and entry.get("class") in table["classes"]
            ]
    return json.dumps(table, indent=2) + "\n"


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o755 if path.suffix == ".sh" else (path.stat().st_mode & 0o777 if path.exists() else 0o644)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def box_sync_timeout(payload_bytes: int, host_load: float) -> int:
    return min(240, math.ceil((60 + payload_bytes / 262144) * (1 + min(3, max(0, host_load)))))


def sync_policy_boxes(home: Path) -> None:
    try:
        hosts = policy_box_hosts(home)
        table = home / ROUTING_TABLE
        expected = {
            "policy_rev": json.loads(table.read_text())["policy_rev"],
            "table_sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
        }
        bins = [home / ".agent-lb/bin" / name for name in ROUTE_FILES]
        tables = [table, table.with_name("quota-swap.json")]
        if not all(path.is_file() for path in bins + tables):
            raise ValueError("routing files missing")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"WARNING: box policy sync unavailable ({type(exc).__name__})", file=sys.stderr)
        return
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    ssh_config = home / ".agent-rails/factory/ssh/hosts.conf"
    if ssh_config.is_file():
        ssh += ["-F", str(ssh_config)]
    verify = (
        'ROUTE_TABLE="$HOME/.agent-lb/managed/coding-agents/routing-table.json" '
        'python3 "$HOME/.agent-lb/bin/route" policy snapshot --json'
    )
    for host in hosts:
        try:
            if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
                raise ValueError("invalid box name")
            load_probe = shlex.quote("import os; print(os.getloadavg()[0] / (os.cpu_count() or 1))")
            preparation = subprocess.run(
                [
                    *ssh,
                    host,
                    'mkdir -p "$HOME/.agent-lb/bin" "$HOME/.agent-lb/managed/coding-agents" "$HOME/.claude/docs/models"'
                    f" && python3 -c {load_probe}",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            host_load = float(preparation.stdout.strip())
            if not math.isfinite(host_load) or host_load < 0:
                raise ValueError("invalid box load")
            timeout = box_sync_timeout(0, host_load)
            for sources, destination in (
                (bins, ".agent-lb/bin/"),
                (tables, ".agent-lb/managed/coding-agents/"),
                (list((home / ".claude/docs/models").glob("*.md")), ".claude/docs/models/"),
            ):
                if not sources:
                    continue
                payload_bytes = sum(path.stat().st_size for path in sources)
                transfer_timeout = box_sync_timeout(payload_bytes, host_load)
                print(f"box policy sync {host}: bytes={payload_bytes} load={host_load:.2f} timeout={transfer_timeout}s")
                subprocess.run(
                    ["rsync", "-az", "-e", shlex.join(ssh), *(str(path) for path in sources), f"{host}:{destination}"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=transfer_timeout,
                )
            mcp_source = Path(__file__).with_name("harness_mcp.py").read_text()
            mcp_timeout = box_sync_timeout(len(mcp_source.encode()), host_load)
            mcp = subprocess.run(
                [*ssh, host, f"timeout {mcp_timeout - 5} python3 -"],
                input=mcp_source,
                capture_output=True,
                text=True,
                check=True,
                timeout=mcp_timeout,
            )
            print(f"box MCP {host}: {mcp.stdout.strip()}")
            result = subprocess.run([*ssh, host, verify], capture_output=True, text=True, check=True, timeout=timeout)
            actual = json.loads(result.stdout)
            if any(actual.get(key) != value for key, value in expected.items()):
                raise ValueError("policy_rev or table_sha256 mismatch")
            print(
                f"box policy synced {host}: policy_rev={expected['policy_rev']} table_sha256={expected['table_sha256']}"
            )
        except subprocess.TimeoutExpired as exc:
            print(
                f"WARNING: box policy sync timed out for {host} after {exc.timeout}s; local install succeeded",
                file=sys.stderr,
            )
        except subprocess.CalledProcessError as exc:
            reason = "remote timeout" if exc.returncode == 124 else f"exit {exc.returncode}"
            print(f"WARNING: box policy sync failed for {host} ({reason}); local install succeeded", file=sys.stderr)
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            print(
                f"WARNING: box policy sync failed for {host} ({type(exc).__name__}); local install succeeded",
                file=sys.stderr,
            )


def checkpoint_changes(home, changes, replace_policy_link=False, policy_link_target=""):
    checkpoint_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    checkpoint = home / ".agent-lb" / "config-checkpoints" / "coding-agents" / checkpoint_name
    checkpoint.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, str] = {}
    for path in changes:
        if path.exists():
            relative = path.relative_to(home)
            destination = checkpoint / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            manifest[str(relative)] = "copied"
        else:
            manifest[str(path.relative_to(home))] = "absent"
    if replace_policy_link:
        manifest[str(POLICY_DIR)] = f"symlink -> {policy_link_target}"

        shutil.copytree(
            home / POLICY_DIR,
            checkpoint / "policy-link-target",
            ignore=shutil.ignore_patterns("__pycache__"),
            symlinks=True,
        )
    (checkpoint / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if replace_policy_link:
        policy_dir = home / POLICY_DIR
        policy_dir.unlink()

        shutil.copytree(checkpoint / "policy-link-target", policy_dir, symlinks=True)
        print(f"replaced symlink {policy_dir} -> {policy_link_target} with an installed copy")
    print(f"checkpoint {checkpoint}")


def autocompact_text(text, window):
    decoder = json.JSONDecoder()
    index = text.index("{") + 1
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if text[index] == "}":
            prefix = "," if json.loads(text) else ""
            return text[:index].rstrip() + prefix + '\n  "autoCompactWindow": ' + str(window) + "\n" + text[index:]
        key, index = decoder.raw_decode(text, index)
        while text[index].isspace() or text[index] == ":":
            index += 1
        start = index
        _, index = decoder.raw_decode(text, index)
        if key == "autoCompactWindow":
            return text[:start] + str(window) + text[index:]
        while text[index].isspace():
            index += 1
        if text[index] == ",":
            index += 1
