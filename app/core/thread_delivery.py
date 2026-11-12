from __future__ import annotations

import json
import shlex
import socket
import subprocess
import sys
from pathlib import Path


def _record(target: str) -> dict | None:
    path = Path.home() / ".agent-rails/agents" / target / "agent.json"
    return json.loads(path.read_text()) if path.is_file() else None


def thread_id(target: str) -> str:
    record = _record(target)
    if record is not None:
        binding = record.get("t3") or {}
        ident = binding.get("thread_id") or binding.get("threadId")
        if not ident:
            raise ValueError(f"{target} has no T3 thread binding")
        return ident
    if target.startswith("w5H:"):
        raise ValueError("Use a T3 thread id or a bound agent handle")
    return target


def on_agent_host(target: str, command: list[str]) -> list[str]:
    record = _record(target)
    host = ((record or {}).get("t3") or {}).get("host")
    local = "studio" if sys.platform == "darwin" else socket.gethostname().split(".")[0]
    if host and host != local:
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, shlex.join(command)]
    return command


def send(target: str, message: str, timeout: int = 30, *, mode: str = "queue") -> subprocess.CompletedProcess:
    command = on_agent_host(target, ["t3", "thread", "send", thread_id(target), message, "--mode", mode])
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
