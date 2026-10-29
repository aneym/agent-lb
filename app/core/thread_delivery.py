from __future__ import annotations

import json
import shlex
import socket
import subprocess
import sys
from pathlib import Path


def thread_id(target: str) -> str:
    path = Path.home() / ".agent-rails/agents" / target / "agent.json"
    if path.is_file():
        record = json.loads(path.read_text())
        binding = record.get("t3") or {}
        ident = binding.get("thread_id") or binding.get("threadId")
        if not ident:
            raise ValueError(f"{target} has no T3 thread binding")
        return ident
    if target.startswith("w5H:"):
        raise ValueError("Use a T3 thread id or a bound agent handle")
    return target


def send(target: str, message: str, timeout: int = 30) -> subprocess.CompletedProcess:
    command = ["t3", "thread", "send", thread_id(target), message, "--mode", "queue"]
    path = Path.home() / ".agent-rails/agents" / target / "agent.json"
    if path.is_file():
        host = (json.loads(path.read_text()).get("t3") or {}).get("host")
        local = "studio" if sys.platform == "darwin" else socket.gethostname().split(".")[0]
        if host and host != local:
            command = ["ssh", "-o", "BatchMode=yes", host, shlex.join(command)]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
