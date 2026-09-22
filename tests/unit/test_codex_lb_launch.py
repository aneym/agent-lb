from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest


def load_launcher_module():
    path = Path(__file__).resolve().parents[2] / "clients" / "codex-lb-launch"
    loader = importlib.machinery.SourceFileLoader("codex_lb_launch_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_tunnel_bucket_uses_configured_aggregate_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CODEX_LB_TUNNEL_RATE_MBPS", "40")

    bucket = launcher.tunnel_bucket_from_env()

    assert bucket is not None
    assert bucket.rate == pytest.approx(5_000_000.0)
    assert bucket.burst_seconds == pytest.approx(1.0)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_tunnel_bucket_can_be_explicitly_disabled(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CODEX_LB_TUNNEL_RATE_MBPS", value)

    assert launcher.tunnel_bucket_from_env() is None


def test_tunnel_bucket_queues_aggregate_bytes_from_two_tunnels(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = load_launcher_module()
    now = [100.0]
    sleeps: list[float] = []
    monkeypatch.setattr(launcher.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(launcher.time, "sleep", lambda duration: sleeps.append(duration))
    bucket = launcher.TunnelBucket(1000.0, burst_bytes=0.0)

    bucket.throttle(1000)
    bucket.throttle(1000)

    assert sum(sleeps) == pytest.approx(1.0)


def test_loopback_is_added_to_no_proxy_without_excluding_external_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("NO_PROXY", "internal.example")

    launcher._append_no_proxy(("127.0.0.1", "localhost", "::1"))

    assert os.environ["NO_PROXY"].split(",") == ["internal.example", "127.0.0.1", "localhost", "::1"]


def test_connect_target_rejects_invalid_or_out_of_range_ports() -> None:
    launcher = load_launcher_module()

    assert launcher._valid_connect_target("example.com:443") == ("example.com", 443)
    assert launcher._valid_connect_target("example.com:0") is None
    assert launcher._valid_connect_target("example.com:65536") is None
    assert launcher._valid_connect_target("example.com") is None


def test_existing_proxy_configuration_is_not_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("HTTPS_PROXY", "http://existing-proxy:8080")

    with pytest.raises(SystemExit, match="refuses to replace"):
        launcher.main([])

    assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:8080"


def test_wrapper_preserves_arguments_and_sets_both_proxy_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = load_launcher_module()
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(launcher, "_start_proxy", lambda: "http://127.0.0.1:45678")
    monkeypatch.setattr(launcher.os, "execvp", lambda command, argv: calls.append((command, argv)))

    launcher.main(["exec", "--model", "gpt-6-astra"])

    assert calls == [("codex", ["codex", "exec", "--model", "gpt-6-astra"])]
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:45678"
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:45678"
