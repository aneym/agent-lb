from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

_cache: dict[str | None, tuple[float, dict[str, Any]]] = {}


async def get_menu(task_class: str | None) -> dict[str, Any] | JSONResponse:
    now = time.monotonic()
    cached = _cache.get(task_class)
    if cached is not None and now - cached[0] < 30:
        return cached[1]
    binary = os.environ.get("AGENT_LB_ROUTE_BIN") or str(Path.home() / ".agent-lb/bin/route")
    argv = [binary, "menu", "--json"]
    if task_class is not None:
        argv.extend(["--class", task_class])
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            raise ValueError("route menu exited unsuccessfully")
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise ValueError("route menu did not return an object")
    except (OSError, asyncio.TimeoutError, ValueError, json.JSONDecodeError) as exc:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.communicate()
        return JSONResponse(status_code=503, content={"error": {"code": "menu_unavailable", "message": str(exc)}})
    result = {**result, "source": "route menu"}
    _cache[task_class] = (now, result)
    return result
