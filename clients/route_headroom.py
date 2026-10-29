from __future__ import annotations

import json
import math
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def number(value: object) -> float | None:
    try:
        result = float(str(value))
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def measured_placement(environments: dict, local: str) -> tuple[str, str]:
    try:
        fleet = json.loads((Path.home() / ".agent-rails/factory-runtime/fleet/config/fleet.json").read_text())
        stale_s = float(fleet.get("admit", {}).get("stale_s", 120))
        token = os.environ.get("NOMAD_TOKEN")
        if not token:
            for name in ("nomad-token", "nomad-node-token"):
                result = subprocess.run(["agent-secret", "get", name], capture_output=True, text=True, timeout=3)
                if result.returncode == 0 and result.stdout.strip():
                    token = result.stdout.strip()
                    break
        if not token:
            raise ValueError("no Nomad credential")
        ca = next(
            (
                Path(path).expanduser()
                for path in (
                    os.environ.get("NOMAD_CACERT", ""),
                    "~/.agent-rails/nomad/tls/ca.pem",
                    "/etc/nomad.d/tls/ca.pem",
                )
                if path and Path(path).expanduser().is_file()
            ),
            None,
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca) if ca else None)),
        )
        bases = (
            [os.environ["NOMAD_ADDR"]]
            if os.environ.get("NOMAD_ADDR")
            else [f"https://{host}:4646" for host in fleet["servers"]]
        )
        for base in bases:
            try:
                request = urllib.request.Request(base.rstrip("/") + "/v1/nodes", headers={"X-Nomad-Token": token})
                with opener.open(request, timeout=2) as response:
                    nodes = json.load(response)
                candidates = []
                for node in nodes:
                    name = node.get("Name")
                    if name not in environments or node.get("Status") != "ready" or node.get("Drain"):
                        continue
                    if node.get("SchedulingEligibility") != "eligible":
                        continue
                    request = urllib.request.Request(
                        base.rstrip("/") + "/v1/node/" + node["ID"], headers={"X-Nomad-Token": token}
                    )
                    with opener.open(request, timeout=2) as response:
                        meta = json.load(response).get("Meta") or {}
                    at = number(meta.get("admit_at"))
                    age = time.time() - at if at is not None else -1
                    cores, mb, room = (
                        number(meta.get(key)) for key in ("admit_free_cores", "admit_free_mb", "admit_room")
                    )
                    if not 0 <= age <= stale_s or meta.get("admit_open") != "1":
                        continue
                    if cores is None or mb is None or room is None or room <= 0 or not number(meta.get("admit_w")):
                        continue
                    candidates.append((cores, room, mb, name, age))
                if candidates:
                    cores, room, mb, name, age = min(candidates, key=lambda row: (-row[0], -row[1], -row[2], row[3]))
                    return name, (
                        f"fresh Nomad admit headroom: free_cores={cores:g} room={room:g} "
                        f"free_mb={mb:g} age_s={age:.1f}; highest measured free cores, then room and memory"
                    )
                return local, "no fresh open Nomad admit headroom; fallback to caller environment"
            except (OSError, ValueError, urllib.error.URLError):
                continue
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    return local, "Nomad admit headroom unavailable; fallback to caller environment"
