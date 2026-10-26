from __future__ import annotations

import hashlib
import json
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


def source_routing_table(directory: Path) -> dict:
    table: dict = {}
    for part in sorted(directory.glob("*.json")):
        value = json.loads(part.read_text())
        if not isinstance(value, dict) or table.keys() & value.keys():
            raise SystemExit(f"error: {part} is not an object or repeats a key")
        table.update(value)
    return table


def desired_routing_table(source_path: Path, live_path: Path) -> str:
    table = source_routing_table(source_path)
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


def _sync_timeout(payload_bytes: int, load_1m: float, cpu_count: int) -> int:
    payload_mib = max(1.0, payload_bytes / (1024 * 1024))
    load_factor = max(1.0, 1.0 + max(0.0, load_1m / max(1, cpu_count) - 0.5))
    return min(300, max(60, round((30 + payload_mib * 12) * load_factor)))


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
        model_docs = list((home / ".claude/docs/models").glob("*.md"))
        sources = [*bins, *tables, *model_docs]
        if not all(path.is_file() for path in bins + tables):
            raise ValueError("routing files missing")
        payload_bytes = sum(path.stat().st_size for path in sources)
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
            load_output = subprocess.run(
                [*ssh, host, "cat /proc/loadavg; nproc"],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            ).stdout.splitlines()
            if len(load_output) < 2:
                raise ValueError("invalid host load output")
            load = load_output[0].split()
            if not load:
                raise ValueError("invalid host load output")
            load_1m = float(load[0])
            cpu_count = int(load_output[1])
            sync_timeout = _sync_timeout(payload_bytes, load_1m, cpu_count)
            print(f"box sync budget {host}: timeout={sync_timeout}s load={load_1m:.2f}/{cpu_count}")
            subprocess.run(
                [
                    *ssh,
                    host,
                    'mkdir -p "$HOME/.agent-lb/bin" "$HOME/.agent-lb/managed/coding-agents" "$HOME/.claude/docs/models"',
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=sync_timeout,
            )
            for sources, destination in (
                (bins, ".agent-lb/bin/"),
                (tables, ".agent-lb/managed/coding-agents/"),
                (model_docs, ".claude/docs/models/"),
            ):
                subprocess.run(
                    ["rsync", "-az", "-e", shlex.join(ssh), *(str(path) for path in sources), f"{host}:{destination}"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=sync_timeout,
                )
            mcp = subprocess.run(
                [*ssh, host, f"timeout {sync_timeout - 5} python3 -"],
                input=Path(__file__).with_name("harness_mcp.py").read_text(),
                capture_output=True,
                text=True,
                check=True,
                timeout=sync_timeout,
            )
            print(f"box MCP {host}: {mcp.stdout.strip()}")
            result = subprocess.run(
                [*ssh, host, verify], capture_output=True, text=True, check=True, timeout=sync_timeout
            )
            actual = json.loads(result.stdout)
            if any(actual.get(key) != value for key, value in expected.items()):
                raise ValueError("policy_rev or table_sha256 mismatch")
            print(
                f"box policy synced {host}: policy_rev={expected['policy_rev']} table_sha256={expected['table_sha256']}"
            )
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
