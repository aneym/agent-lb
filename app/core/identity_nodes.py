"""Background tailnet node discovery for request attribution.

Only node IPs and short DNS labels leave this module. In particular, status JSON's
User and login fields are never inspected, retained or logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from ipaddress import ip_address
from typing import Any

from app.core.config.settings import Settings, identity_handle
from app.core.identity import set_node_lookup

logger = logging.getLogger(__name__)
_REFRESH_SECONDS = 60
_TIMEOUT_SECONDS = 5
_WARNING_SECONDS = 3600


def _nodes_from_status(raw: bytes) -> dict[str, str]:
    status = json.loads(raw)
    if not isinstance(status, dict):
        raise ValueError("invalid status")
    nodes: dict[str, str] = {}
    peers = status.get("Peer")
    entries: list[Any] = [status.get("Self")]
    if isinstance(peers, dict):
        entries.extend(peers.values())
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        dns_name = entry.get("DNSName")
        ips = entry.get("TailscaleIPs")
        if not isinstance(dns_name, str) or not isinstance(ips, list):
            continue
        handle = identity_handle(dns_name.rstrip(".").split(".", 1)[0], 48)
        if handle is None:
            continue
        for ip in ips:
            if not isinstance(ip, str):
                continue
            try:
                nodes[str(ip_address(ip))] = handle
            except ValueError:
                continue
    return nodes


class IdentityNodeCache:
    """Lifespan-owned refresher; requests only read its last successful snapshot."""

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.identity_enabled
        self._bin = settings.identity_tailscale_bin
        self._task: asyncio.Task[None] | None = None
        self._last_warning = float("-inf")
        self._nodes: dict[str, str] = {}

    async def start(self) -> None:
        set_node_lookup(None)
        if not self._enabled:
            return
        if not shutil.which(self._bin):
            logger.warning(
                "Tailnet node cache disabled: tailscale binary not found (configure AGENT_LB_IDENTITY_TAILSCALE_BIN)"
            )
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        set_node_lookup(None)

    async def _refresh(self) -> None:
        process = await asyncio.create_subprocess_exec(
            self._bin, "status", "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=_TIMEOUT_SECONDS)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode != 0:
            raise RuntimeError("status command failed")
        nodes = _nodes_from_status(stdout)
        self._nodes = nodes
        set_node_lookup(nodes)

    async def _run(self) -> None:
        while True:
            try:
                await self._refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                now = time.monotonic()
                if now - self._last_warning >= _WARNING_SECONDS:
                    logger.warning("Tailnet node cache refresh failed; keeping last good snapshot")
                    self._last_warning = now
            await asyncio.sleep(_REFRESH_SECONDS)
