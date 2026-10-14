

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.settings.api import _resolve_runtime_connect_address

pytestmark = pytest.mark.unit


def _fake_request(hostname: str | None) -> Any:

    return SimpleNamespace(url=SimpleNamespace(hostname=hostname))





def test_env_override_wins_over_request_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_CONNECT_ADDRESS", "lb.internal.example:2455")
    request = _fake_request("10.0.0.5")
    assert _resolve_runtime_connect_address(request) == "lb.internal.example:2455"


def test_env_override_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_CONNECT_ADDRESS", "  lb.internal:2455  ")
    request = _fake_request("127.0.0.1")
    assert _resolve_runtime_connect_address(request) == "lb.internal:2455"


def test_env_override_ignored_when_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_CONNECT_ADDRESS", "   ")
    request = _fake_request("10.0.0.5")
    assert _resolve_runtime_connect_address(request) == "10.0.0.5"





def test_non_loopback_ipv4_host_returned_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LB_CONNECT_ADDRESS", raising=False)
    request = _fake_request("192.168.1.42")


    assert _resolve_runtime_connect_address(request) == "192.168.1.42"





def _patch_resolver(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[str]]) -> None:


    def _fake(hostname, port, family=0, type=0, proto=0, flags=0):  # noqa: ANN001
        if hostname not in mapping:
            raise OSError(f"name or service not known: {hostname}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in mapping[hostname]]

    monkeypatch.setattr("app.modules.settings.api.socket.getaddrinfo", _fake)


def test_resolvable_hostname_returns_resolved_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LB_CONNECT_ADDRESS", raising=False)
    _patch_resolver(monkeypatch, {"lb.internal": ["10.0.0.5"]})
    request = _fake_request("lb.internal")
    assert _resolve_runtime_connect_address(request) == "10.0.0.5"


def test_resolvable_hostname_skips_loopback_ip(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.delenv("AGENT_LB_CONNECT_ADDRESS", raising=False)
    _patch_resolver(monkeypatch, {"lb.internal": ["127.0.0.1", "10.0.0.5"]})
    request = _fake_request("lb.internal")
    assert _resolve_runtime_connect_address(request) == "10.0.0.5"


def test_hostname_resolving_only_to_loopback_falls_back_to_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_LB_CONNECT_ADDRESS", raising=False)
    _patch_resolver(monkeypatch, {"lb.internal": ["127.0.0.1"]})
    request = _fake_request("lb.internal")
    assert _resolve_runtime_connect_address(request) == "lb.internal"


def test_unresolvable_hostname_falls_back_to_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LB_CONNECT_ADDRESS", raising=False)
    _patch_resolver(monkeypatch, {})
    request = _fake_request("lb.unknown")
    assert _resolve_runtime_connect_address(request) == "lb.unknown"





@pytest.mark.parametrize(
    "hostname",
    ["localhost", "127.0.0.1", "::1", "[::1]"],
)
def test_loopback_host_returns_placeholder(monkeypatch: pytest.MonkeyPatch, hostname: str) -> None:
    monkeypatch.delenv("AGENT_LB_CONNECT_ADDRESS", raising=False)
    request = _fake_request(hostname)
    assert _resolve_runtime_connect_address(request) == "<agent-lb-ip-or-dns>"


def test_missing_host_returns_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LB_CONNECT_ADDRESS", raising=False)
    request = _fake_request(None)
    assert _resolve_runtime_connect_address(request) == "<agent-lb-ip-or-dns>"


def test_empty_host_returns_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LB_CONNECT_ADDRESS", raising=False)
    request = _fake_request("")
    assert _resolve_runtime_connect_address(request) == "<agent-lb-ip-or-dns>"
