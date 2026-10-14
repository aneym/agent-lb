

from __future__ import annotations

import pytest

import app.core.middleware.firewall_cache as firewall_cache
from app.core.middleware.firewall_cache import (
    DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS,
    FirewallIPCache,
    get_firewall_ip_cache,
    reset_firewall_ip_cache_for_testing,
)

pytestmark = pytest.mark.unit


def test_default_ttl_is_thirty_seconds() -> None:
    assert DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS == 30
    cache = FirewallIPCache()
    assert cache.ttl_seconds == 30


def test_singleton_respects_settings_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_firewall_ip_cache_for_testing()

    class _Settings:
        firewall_ip_cache_ttl_seconds = 120

    monkeypatch.setattr(firewall_cache, "_resolve_configured_ttl", lambda: _Settings.firewall_ip_cache_ttl_seconds)

    cache = get_firewall_ip_cache()
    assert cache.ttl_seconds == 120


def test_singleton_falls_back_to_default_when_settings_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_firewall_ip_cache_for_testing()

    def _raise() -> int:
        raise RuntimeError("settings not initialised")

    monkeypatch.setattr(firewall_cache, "_resolve_configured_ttl", lambda: DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS)



    cache = get_firewall_ip_cache()
    assert cache.ttl_seconds == DEFAULT_FIREWALL_IP_CACHE_TTL_SECONDS


def test_invalid_ttl_rejected() -> None:
    with pytest.raises(ValueError):
        FirewallIPCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        FirewallIPCache(ttl_seconds=-1)


def teardown_module(_module: object) -> None:

    reset_firewall_ip_cache_for_testing()
