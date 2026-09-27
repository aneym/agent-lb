"""Node discovery retains only tailnet names and does not block request resolution."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging

import pytest

import app.core.identity_nodes as nodes_module
from app.core.config.settings import Settings
from app.core.identity import resolve_identity, set_node_lookup
from app.core.identity_nodes import IdentityNodeCache

TAIL_A = str(ipaddress.ip_network("100.64.0.0/10")[7])
TAIL_MISS = str(ipaddress.ip_network("100.64.0.0/10")[8])
FAKE_LOGIN = "fake-login@invalid.test"


class FakeProcess:
    def __init__(self, output: bytes, code: int = 0) -> None:
        self.output = output
        self.returncode = code

    async def communicate(self):
        return self.output, b""


@pytest.mark.asyncio
async def test_refresh_keeps_only_node_handles_and_last_good_snapshot(monkeypatch, caplog):
    settings = Settings(
        identity_owner="owner-a",
        identity_owner_machines="box-1",
        identity_local_machine="box-1",
        firewall_trust_proxy_headers=True,
    )
    cache = IdentityNodeCache(settings)
    payload = json.dumps(
        {
            "User": {"1": {"LoginName": FAKE_LOGIN}},
            "Self": {"DNSName": "box-1.example.invalid.", "TailscaleIPs": [TAIL_A], "UserID": 1},
            "Peer": {"a": {"DNSName": "box-2.example.invalid.", "TailscaleIPs": [TAIL_MISS]}},
        }
    ).encode()
    calls = 0

    async def fake_exec(*args, **kwargs):
        nonlocal calls
        assert args[1:] == ("status", "--json")
        calls += 1
        return FakeProcess(payload if calls == 1 else b"", 0 if calls == 1 else 1)

    monkeypatch.setattr(nodes_module.asyncio, "create_subprocess_exec", fake_exec)
    headers = {"x-forwarded-for": TAIL_A}
    try:
        with caplog.at_level(logging.WARNING):
            await cache._refresh()
            assert cache._nodes == {TAIL_A: "box-1", TAIL_MISS: "box-2"}
            assert resolve_identity(headers, "127.0.0.1", settings=settings).caller_machine == "box-1"
            assert calls == 1  # resolution reads only the snapshot
            assert (
                resolve_identity(
                    {"x-forwarded-for": str(ipaddress.ip_network("100.64.0.0/10")[9])}, "127.0.0.1", settings=settings
                ).caller_machine
                == "tailnet-unknown"
            )
            with pytest.raises(RuntimeError, match="status command failed"):
                await cache._refresh()
            assert resolve_identity(headers, "127.0.0.1", settings=settings).caller_machine == "box-1"
        assert FAKE_LOGIN not in str(cache._nodes)
        assert all(FAKE_LOGIN not in record.getMessage() for record in caplog.records)
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_missing_binary_and_disabled_identity_do_not_start_task(monkeypatch, caplog):
    monkeypatch.setattr(nodes_module.shutil, "which", lambda path: None)
    for enabled in (True, False):
        cache = IdentityNodeCache(Settings(identity_enabled=enabled, identity_tailscale_bin="not-installed"))
        await cache.start()
        assert cache._task is None
        await cache.stop()
    assert len([r for r in caplog.records if "binary not found" in r.message]) == 1
    monkeypatch.setattr(nodes_module.shutil, "which", lambda path: "/bin/true")
    cache = IdentityNodeCache(Settings(identity_enabled=False))
    await cache.start()
    assert cache._task is None
    await cache.stop()


@pytest.mark.asyncio
async def test_refresh_failure_warns_once_per_hour(monkeypatch, caplog):
    cache = IdentityNodeCache(Settings())

    async def fail():
        raise RuntimeError(FAKE_LOGIN)

    sleeps = 0

    async def finish_after_three(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            cache._last_warning -= 3601
        if sleeps == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(cache, "_refresh", fail)
    monkeypatch.setattr(nodes_module.asyncio, "sleep", finish_after_three)
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await cache._run()
    assert len([record for record in caplog.records if record.name == nodes_module.__name__]) == 2
    assert all(FAKE_LOGIN not in record.getMessage() for record in caplog.records)
    set_node_lookup(None)
