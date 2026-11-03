from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def threshold(name: str, default: float) -> float:
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid {name}")
    return value


def probe(host: str, local: bool) -> dict:
    source = Path(__file__).with_name("route_host_probe.py").read_text()
    argv = (
        ["python3", "-c", source]
        if local
        else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", host, "python3 -c " + shlex.quote(source)]
    )
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=12, check=True)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"error": "host readiness probe unavailable"}


def rejection(sample: dict) -> str | None:
    if sample.get("error"):
        return sample["error"]
    try:
        if not -30 <= time.time() - float(sample["at"]) <= 120:
            return "host readiness probe stale"
        available = float(sample["available_mb"])
        full = float(sample["full_avg60"])
        count = float(sample["codex_servers"])
        if not all(math.isfinite(value) and value >= 0 for value in (available, full, count)):
            return "host readiness probe invalid"
        floor = max(threshold("ROUTE_MIN_AVAILABLE_MB", 3072), 1536 * (count + 1))
        if available < floor:
            return f"available memory {available:.0f} MB below {floor:g} MB floor"
        if full > threshold("ROUTE_MAX_MEMORY_FULL_AVG60", 20):
            return f"memory pressure full {full:g}%"
        timeout = sample.get("timeout_at")
        if timeout is not None and -30 <= time.time() - float(timeout) < 900:
            return "Codex thread/start timeout in last 15 min"
    except (KeyError, TypeError, ValueError):
        return "host readiness probe invalid"
    return None


def host_rejections(environments: dict, local: str) -> dict[str, str]:
    with ThreadPoolExecutor(max_workers=len(environments)) as pool:
        samples = pool.map(lambda name: probe(environments[name]["ssh_host"], name == local), environments)
        return {name: reason for name, sample in zip(environments, samples) if (reason := rejection(sample))}
