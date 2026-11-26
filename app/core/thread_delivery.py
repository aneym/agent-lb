from __future__ import annotations

import json
import re
import shlex
import socket
import subprocess
import sys
from pathlib import Path


def _record(target: str) -> dict | None:
    path = Path.home() / ".agent-rails/agents" / ("factory" if target == "factory-office" else target) / "agent.json"
    record = json.loads(path.read_text()) if path.is_file() else None
    if target == "factory-office":
        binding = (record or {}).get("t3") or {}
        office = binding.get("office")
        if not office:
            host = binding.get("host", "")
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", host):
                raise ValueError("factory has no valid main host for office lookup")
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
                 'PATH="$HOME/.local/bin:$PATH" t3 agent list --json'],
                capture_output=True, text=True, timeout=15, check=True,
            )
            office = next((home.get("office") for home in json.loads(result.stdout)["homes"]
                           if home.get("handle") == "factory"), None)
        office = office or {}
        thread = office.get("thread_id") or office.get("threadId")
        if not thread or not office.get("host") or thread == binding.get("thread_id"):
            raise ValueError("factory has no valid office binding")
        return {"t3": office}
    return record


def _thread_id(target: str, record: dict | None) -> str:
    if record is not None:
        binding = record.get("t3") or {}
        ident = binding.get("thread_id") or binding.get("threadId")
        if not ident:
            raise ValueError(f"{target} has no T3 thread binding")
        return ident
    if target.startswith("w5H:"):
        raise ValueError("Use a T3 thread id or a bound agent handle")
    return target


def thread_id(target: str) -> str:
    return _thread_id(target, _record(target))


def _on_agent_host(record: dict | None, command: list[str]) -> list[str]:
    host = ((record or {}).get("t3") or {}).get("host")
    local = "studio" if sys.platform == "darwin" else socket.gethostname().split(".")[0]
    if host and host != local:
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, shlex.join(command)]
    return command


def on_agent_host(target: str, command: list[str]) -> list[str]:
    return _on_agent_host(_record(target), command)


def send(target: str, message: str, timeout: int = 30, *, mode: str = "queue") -> subprocess.CompletedProcess:
    record = _record(target)
    command = _on_agent_host(record, ["t3", "thread", "send", _thread_id(target, record), message, "--mode", mode])
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
