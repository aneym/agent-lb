from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_install_syncs_and_verifies_boxes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "config/coding-agents"
    shutil.copytree(ROOT / "config/coding-agents", source)
    (repo / "clients").mkdir()
    for name in ("route", "seat"):
        shutil.copy2(ROOT / "clients" / name, repo / "clients" / name)
    for args in (("init",), ("add", "."),
                 ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"),
                 ("branch", "-M", "main"), ("remote", "add", "origin", str(repo))):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    home = tmp_path / "home"
    registry = home / ".agent-rails/factory"
    registry.mkdir(parents=True)
    hosts = ["ax42", "pc-wsl", "forge"]
    (registry / "machines.json").write_text(json.dumps([
        *({"name": host, "job_host": True, "os": "linux", "aliases": [host + "-alias"]} for host in hosts),
        {"name": "studio", "job_host": True, "os": "macos"},
        {"name": "disabled", "job_host": True, "enabled": False},
    ]))
    (registry / "boxes.json").write_text(json.dumps({"boxes": [
        {"name": host, "kind": "plain", "enabled": True} for host in hosts
    ]}))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.jsonl"
    remote = tmp_path / "remote"
    fake = f'''#!{sys.executable}
import json, os, pathlib, shutil, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["SYNC_LOG"], "a") as stream:
    stream.write(json.dumps([name, *args]) + "\\n")
remote = pathlib.Path(os.environ["SYNC_REMOTE"])
if name == "rsync":
    host, destination = args[-1].split(":", 1)
    target = remote / host / destination
    target.mkdir(parents=True, exist_ok=True)
    for item in args[3:-1]:
        shutil.copy2(item, target)
else:
    host, command = args[-2:]
    if host == os.environ.get("SYNC_UNREACHABLE"):
        sys.exit(255)
    if "policy snapshot --json" in command:
        env = os.environ | {{"HOME": str(remote / host), "ROUTE_TABLE": str(remote / host / ".agent-lb/managed/coding-agents/routing-table.json")}}
        result = subprocess.run([sys.executable, str(remote / host / ".agent-lb/bin/route"), "policy", "snapshot", "--json"], env=env, capture_output=True, text=True, check=True)
        snapshot = json.loads(result.stdout)
        if host == os.environ.get("SYNC_MISMATCH"):
            snapshot["table_sha256"] = "wrong"
        print(json.dumps(snapshot))
'''
    for name in ("ssh", "rsync"):
        path = fake_bin / name
        path.write_text(fake)
        path.chmod(0o755)
    env = os.environ | {"HOME": str(home), "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
                        "FACTORY_HOME": str(registry), "FACTORY_MACHINES": str(registry / "machines.json"),
                        "FLEET_BOXES_FILE": str(registry / "boxes.json"),
                        "SYNC_LOG": str(log), "SYNC_REMOTE": str(remote),
                        "AGENT_LB_URL": "http://127.0.0.1:1"}
    for key in ("ROUTE_TABLE", "ROUTE_FIXTURE_DIR", "ROUTE_MODELS_CACHE"):
        env.pop(key, None)

    def install(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(source / "install-policy.py"), "--home", str(home), *args],
                                env=env | extra, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    result = install()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    table = home / ".agent-lb/managed/coding-agents/routing-table.json"
    expected_sha = hashlib.sha256(table.read_bytes()).hexdigest()
    for host in hosts:
        assert f"box policy synced {host}:" in result.stdout
        copies = [call for call in calls if call[0] == "rsync" and call[-1].startswith(host + ":")]
        assert len(copies) == 2
        assert copies[0][-1] == host + ":.agent-lb/bin/"
        assert copies[0][4:-1] == [str(home / ".agent-lb/bin/route"), str(home / ".agent-lb/bin/seat")]
        assert copies[1][-1] == host + ":.agent-lb/managed/coding-agents/"
        assert copies[1][4:-1] == [str(table)]
        ssh = [call for call in calls if call[0] == "ssh" and call[-2] == host]
        assert len(ssh) == 2
        assert "mkdir -p" in ssh[0][-1]
        assert 'ROUTE_TABLE="$HOME/.agent-lb/managed/coding-agents/routing-table.json"' in ssh[1][-1]
        assert 'python3 "$HOME/.agent-lb/bin/route" policy snapshot --json' in ssh[1][-1]
        for relative in (".agent-lb/bin/route", ".agent-lb/bin/seat", ".agent-lb/managed/coding-agents/routing-table.json"):
            assert (remote / host / relative).read_bytes() == (home / relative).read_bytes()
        assert expected_sha in result.stdout
    assert not any("--delete" in arg for call in calls for arg in call)
    assert len(calls) == 12
    for flag in ("--no-box-sync", "--check", "--print"):
        log.write_text("")
        install(flag)
        assert log.read_text() == ""
    log.write_text("")
    result = install(SYNC_UNREACHABLE="ax42", SYNC_MISMATCH="pc-wsl")
    assert "WARNING: box policy sync failed for ax42" in result.stderr
    assert "WARNING: box policy sync failed for pc-wsl" in result.stderr
    assert "box policy synced forge:" in result.stdout
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(call[0] == "rsync" and call[-1].startswith("ax42:") for call in calls)
