from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from codex_home import FLOOR_COMMANDS  # type: ignore[missing-import]


def rpc(proc: subprocess.Popen[str], request_id: int, method: str, params: dict[str, object]) -> dict[str, Any]:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    for line in proc.stdout:
        message = json.loads(line)
        if message.get("id") == request_id:
            if "error" in message:
                raise ValueError(f"{method}: {message['error']}")
            return message.get("result") or {}
    raise ValueError(f"{method}: app-server closed")


def trust_floor_hooks(home: Path, timeout: float = 90) -> str:
    codex, hooks_file = shutil.which("codex"), home / ".codex" / "hooks.json"
    if not codex or not hooks_file.is_file():
        return "skipped: no codex or no hooks.json"
    env = dict(os.environ, CODEX_HOME=str(home / ".codex"))
    proc = subprocess.Popen(
        [codex, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        rpc(proc, 1, "initialize", {"clientInfo": {"name": "coding-agents-install", "version": "1"}})
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        listing = rpc(proc, 2, "hooks/list", {"cwds": [str(home)]})
        entries = listing.get("data") or []
        hooks = [hook for entry in entries if isinstance(entry, dict) for hook in entry.get("hooks") or []]
        pending = {
            hook["key"]: {"trusted_hash": hook["currentHash"]}
            for hook in hooks
            if hook.get("command") in FLOOR_COMMANDS
            and str(hook.get("key", "")).startswith(f"{hooks_file}:")
            and hook.get("trustStatus") in {"untrusted", "modified"}
        }
        if not pending:
            return "floor hooks already trusted"
        config = home / ".codex" / "config.toml"
        if config.is_file():
            shutil.copy2(config, config.with_name("config.toml.bak-hook-trust"))
        edit = {"keyPath": "hooks.state", "value": pending, "mergeStrategy": "upsert"}
        rpc(proc, 3, "config/batchWrite", {"edits": [edit], "reloadUserConfig": False})
        return "trusted " + ", ".join(sorted(pending))
    except (OSError, ValueError, KeyError) as exc:
        return f"failed: {exc}"
    finally:
        timer.cancel()
        proc.kill()
        proc.wait()
