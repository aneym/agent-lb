"""Request attribution: who (member key or owner machine) and where (the auth client IP)."""

from __future__ import annotations

import asyncio
import ipaddress

import pytest
from starlette.requests import HTTPConnection

import app.core.identity as identity_module
from app.core.config.settings import Settings, get_settings
from app.core.identity import RequestIdentity, attach_member, resolve_identity
from app.core.request_locality import resolve_request_client_host

_TAILNET = ipaddress.ip_network("100.64.0.0/10")
TAIL_A = str(_TAILNET[7])
TAIL_B = str(_TAILNET[8])
TAIL_MISS = str(_TAILNET[9])
V6_A = str(ipaddress.ip_network("fd7a:115c:a1e0::/48")[7])
PUBLIC = str(ipaddress.ip_network("192.0.2.0/24")[4])
LOOPBACK = "127.0.0.1"

NODES = {TAIL_A: "laptop", TAIL_B: "peer-1", V6_A: "laptop"}
BASE_SETTINGS = {
    "identity_owner": "owner-a",
    "identity_owner_machines": "box-1,box-2",
    "identity_local_machine": "box-1",
    "identity_machine_aliases": "laptop=box-2",
    "firewall_trust_proxy_headers": True,
}

OWNER_LOCAL = ("owner-a", "owner-machine", "box-1", "local")
MEMBER_A = ("member-id-a", "member-a")


def _case(case_id, headers, socket_ip, expected, *, member=None, settings=None):
    """``member`` is (member id, looked-up name) for a validated member key."""
    return pytest.param(headers, socket_ip, member or (None, None), settings or {}, expected, id=case_id)


@pytest.mark.parametrize(
    ("headers", "socket_ip", "member", "overrides", "expected"),
    [
        # Person (spec change 1)
        _case("keyless-local-owner-machine", {}, LOOPBACK, OWNER_LOCAL),
        _case("member-key-local", {}, LOOPBACK, ("member-a", "member", "box-1", "local"), member=MEMBER_A),
        _case(
            "member-key-from-non-owner-tailnet",
            {"x-forwarded-for": TAIL_B},
            LOOPBACK,
            ("member-a", "member", "peer-1", "tailnet"),
            member=MEMBER_A,
        ),
        _case(
            "member-key-whose-member-is-gone-is-unknown-not-owner",
            {},
            LOOPBACK,
            ("unknown", "unknown", "box-1", "local"),
            member=("member-id-gone", None),
        ),
        _case(
            "member-named-with-email-is-unknown",
            {},
            LOOPBACK,
            ("unknown", "unknown", "box-1", "local"),
            member=("member-id-b", "Member.B@example.com"),
        ),
        _case(
            "member-named-with-compatibility-at-sign-is-unknown",
            {},
            LOOPBACK,
            ("unknown", "unknown", "box-1", "local"),
            member=("member-id-c", "Member.B＠example.com"),
        ),
        # Machine (spec change 2)
        _case(
            "keyless-owner-tailnet-via-alias",
            {"x-forwarded-for": TAIL_A},
            LOOPBACK,
            ("owner-a", "owner-machine", "box-2", "tailnet"),
        ),
        _case(
            "keyless-non-owner-tailnet-is-unknown",
            {"x-forwarded-for": TAIL_B},
            LOOPBACK,
            ("unknown", "unknown", "peer-1", "tailnet"),
        ),
        _case(
            "tailnet-node-cache-miss",
            {"x-forwarded-for": TAIL_MISS},
            LOOPBACK,
            ("unknown", "unknown", "tailnet-unknown", "tailnet"),
        ),
        _case("ipv6-tailnet", {"x-forwarded-for": V6_A}, LOOPBACK, ("owner-a", "owner-machine", "box-2", "tailnet")),
        _case(
            "multi-hop-xff-uses-hop-next-to-trusted-proxy",
            {"x-forwarded-for": f"{TAIL_B}, {TAIL_A}"},
            LOOPBACK,
            ("owner-a", "owner-machine", "box-2", "tailnet"),
        ),
        _case(
            "multi-hop-xff-through-trusted-tailnet-proxy",
            {"x-forwarded-for": f"{TAIL_B}, {TAIL_A}"},
            LOOPBACK,
            ("unknown", "unknown", "peer-1", "tailnet"),
            settings={"firewall_trusted_proxy_cidrs": f"{LOOPBACK}/32,{TAIL_A}/32"},
        ),
        _case(
            "xff-ignored-when-proxy-headers-untrusted",
            {"x-forwarded-for": TAIL_A},
            LOOPBACK,
            ("unknown", "unknown", "remote", "remote"),
            settings={"firewall_trust_proxy_headers": False},
        ),
        _case(
            "unparseable-xff-is-remote",
            {"x-forwarded-for": "not-an-ip"},
            LOOPBACK,
            ("unknown", "unknown", "remote", "remote"),
        ),
        _case(
            "funnel-through-serve",
            {"tailscale-funnel-request": "?1", "x-forwarded-for": PUBLIC},
            LOOPBACK,
            ("unknown", "unknown", "public", "funnel"),
        ),
        _case(
            "funnel-header-wins-over-loopback",
            {"tailscale-funnel-request": "?1"},
            LOOPBACK,
            ("unknown", "unknown", "public", "funnel"),
        ),
        _case("remote-socket", {}, PUBLIC, ("unknown", "unknown", "remote", "remote")),
        _case(
            "ipv4-mapped-loopback-follows-auth",
            {"x-agent-lb-machine": "box-2"},
            "::ffff:127.0.0.1",
            ("owner-a", "owner-machine", "box-2", "claimed"),
        ),
        _case(
            "ipv4-mapped-tailnet-not-promoted-above-auth",
            {"x-forwarded-for": f"::ffff:{TAIL_A}"},
            LOOPBACK,
            ("unknown", "unknown", "remote", "remote"),
        ),
        _case(
            "remote-through-trusted-proxy",
            {"x-forwarded-for": PUBLIC},
            LOOPBACK,
            ("unknown", "unknown", "remote", "remote"),
        ),
        _case(
            "owner-list-never-covers-remote",
            {"x-agent-lb-machine": "box-2"},
            PUBLIC,
            ("unknown", "unknown", "remote", "remote"),
            settings={"identity_owner_machines": "remote,public,box-2"},
        ),
        _case(
            "owner-list-never-covers-funnel",
            {"tailscale-funnel-request": "?1", "x-agent-lb-machine": "box-2"},
            LOOPBACK,
            ("unknown", "unknown", "public", "funnel"),
            settings={"identity_owner_machines": "public,box-2"},
        ),
        _case(
            "claimed-valid", {"x-agent-lb-machine": "tunnel-1"}, LOOPBACK, ("unknown", "unknown", "tunnel-1", "claimed")
        ),
        _case(
            "claimed-owner-machine",
            {"x-agent-lb-machine": "box-2"},
            LOOPBACK,
            ("owner-a", "owner-machine", "box-2", "claimed"),
        ),
        _case(
            "claim-cannot-raise-non-owner-local",
            {"x-agent-lb-machine": "box-2"},
            LOOPBACK,
            ("unknown", "unknown", "box-2", "claimed"),
            settings={"identity_local_machine": "guest-box"},
        ),
        _case(
            "non-owner-local-without-claim",
            {},
            LOOPBACK,
            ("unknown", "unknown", "guest-box", "local"),
            settings={"identity_local_machine": "guest-box"},
        ),
        _case(
            "owner-machine-config-slugified",
            {"x-agent-lb-machine": "my-box"},
            LOOPBACK,
            ("owner-a", "owner-machine", "my-box", "claimed"),
            settings={"identity_owner_machines": "my_box,box-1"},
        ),
        _case("claimed-invalid-characters", {"x-agent-lb-machine": "Bad!"}, LOOPBACK, OWNER_LOCAL),
        _case("claimed-too-long", {"x-agent-lb-machine": "a" * 49}, LOOPBACK, OWNER_LOCAL),
        _case(
            "claim-ignored-off-loopback",
            {"x-forwarded-for": TAIL_B, "x-agent-lb-machine": "box-1"},
            LOOPBACK,
            ("unknown", "unknown", "peer-1", "tailnet"),
        ),
        _case(
            "claim-ignored-from-non-loopback-socket-even-if-forwarded-ip-is-loopback",
            {"x-forwarded-for": LOOPBACK, "x-agent-lb-machine": "box-2"},
            TAIL_B,
            ("unknown", "unknown", "remote", "remote"),
            settings={"firewall_trusted_proxy_cidrs": f"{TAIL_B}/32"},
        ),
        _case(
            "non-loopback-socket-forwarding-loopback-cannot-name-owner",
            {"x-forwarded-for": LOOPBACK},
            TAIL_B,
            ("unknown", "unknown", "remote", "remote"),
            settings={"firewall_trusted_proxy_cidrs": f"{TAIL_B}/32"},
        ),
        _case(
            "direct-tailnet-connection-from-owner-node", {}, TAIL_A, ("owner-a", "owner-machine", "box-2", "tailnet")
        ),
        _case(
            "owner-node-address-from-non-loopback-trusted-proxy-is-not-owner",
            {"x-forwarded-for": TAIL_A},
            TAIL_B,
            ("unknown", "unknown", "box-2", "tailnet"),
            settings={"firewall_trusted_proxy_cidrs": f"{TAIL_B}/32"},
        ),
        _case(
            "owner-node-address-behind-serve-and-a-trusted-tailnet-proxy-is-not-owner",
            {"x-forwarded-for": f"{TAIL_A}, {TAIL_B}"},
            LOOPBACK,
            ("unknown", "unknown", "box-2", "tailnet"),
            settings={"firewall_trusted_proxy_cidrs": f"{LOOPBACK}/32,{TAIL_B}/32"},
        ),
        _case(
            "loopback-behind-serve-and-a-trusted-tailnet-proxy-is-remote-even-with-a-claim",
            {"x-forwarded-for": f"{LOOPBACK}, {TAIL_B}", "x-agent-lb-machine": "box-2"},
            LOOPBACK,
            ("unknown", "unknown", "remote", "remote"),
            settings={"firewall_trusted_proxy_cidrs": f"{LOOPBACK}/32,{TAIL_B}/32"},
        ),
        _case(
            "disabled-records-nothing",
            {"x-forwarded-for": TAIL_A},
            LOOPBACK,
            (None, None, None, None),
            member=MEMBER_A,
            settings={"identity_enabled": False},
        ),
    ],
)
def test_resolve_identity(headers, socket_ip, member, overrides, expected):
    settings = Settings(**{**BASE_SETTINGS, **overrides})
    member_id, member_name = member

    resolved = resolve_identity(
        headers,
        socket_ip,
        member_id=member_id,
        member_name=member_name,
        node_cache=NODES,
        settings=settings,
    )

    assert resolved == RequestIdentity(*expected)


@pytest.mark.parametrize(
    ("trust", "headers", "auth_ip", "machine_source"),
    [
        pytest.param(True, {"x-forwarded-for": PUBLIC}, PUBLIC, "remote", id="forged-public-xff"),
        pytest.param(True, {"x-forwarded-for": TAIL_A}, TAIL_A, "tailnet", id="forged-tailnet-xff"),
        pytest.param(True, {"x-forwarded-for": "not-an-ip"}, None, "remote", id="garbage-xff"),
        pytest.param(True, {"x-forwarded-for": f"{PUBLIC}, {LOOPBACK}"}, PUBLIC, "remote", id="xff-ending-in-loopback"),
        pytest.param(True, {"x-forwarded-for": LOOPBACK}, LOOPBACK, "local", id="xff-says-loopback"),
        pytest.param(True, {"x-real-ip": TAIL_B}, TAIL_B, "tailnet", id="x-real-ip-without-xff"),
        pytest.param(True, {"forwarded": f"for={TAIL_A}"}, TAIL_A, "tailnet", id="forwarded-header"),
        pytest.param(False, {"x-forwarded-for": PUBLIC}, LOOPBACK, "remote", id="untrusted-proxy-headers"),
    ],
)
def test_forged_forwarding_on_loopback_follows_the_auth_resolver(monkeypatch, trust, headers, auth_ip, machine_source):
    try:
        with monkeypatch.context() as env:
            env.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true" if trust else "false")
            env.setenv("AGENT_LB_IDENTITY_LOCAL_MACHINE", "box-1")
            get_settings.cache_clear()
            scope = {
                "type": "http",
                "client": (LOOPBACK, 50000),
                "server": ("127.0.0.1", 2455),
                "headers": [(name.encode(), value.encode()) for name, value in headers.items()],
            }
            assert resolve_request_client_host(HTTPConnection(scope)) == auth_ip
            resolved = resolve_identity(headers, LOOPBACK, node_cache=NODES, settings=get_settings())
            assert resolved.caller_machine_source == machine_source
    finally:
        # The context has restored the environment; discard the now-stale Settings too.
        get_settings.cache_clear()


def test_missing_local_machine_never_exposes_os_hostname(monkeypatch):
    monkeypatch.delenv("AGENT_LB_IDENTITY_LOCAL_MACHINE", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "private-persons-laptop")
    settings = Settings(**{key: value for key, value in BASE_SETTINGS.items() if key != "identity_local_machine"})
    assert settings.identity_local_machine is None
    assert resolve_identity({}, LOOPBACK, settings=settings).caller_machine == "local"


def test_alias_keys_and_values_use_node_and_owner_handle_normalization():
    settings = Settings(
        **{
            **BASE_SETTINGS,
            "identity_machine_aliases": "my_laptop=OWNER_BOX,peer-1=Peer Device",
            "identity_owner_machines": "owner_box",
        }
    )
    assert settings.identity_machine_aliases == {"my-laptop": "owner-box", "peer-1": "peer-device"}
    resolved = resolve_identity(
        {"x-forwarded-for": TAIL_A},
        LOOPBACK,
        node_cache={TAIL_A: "my-laptop"},
        settings=settings,
    )
    assert resolved == RequestIdentity("owner-a", "owner-machine", "owner-box", "tailnet")


def test_bad_machine_alias_entry_does_not_prevent_settings_loading():
    settings = Settings(**{**BASE_SETTINGS, "identity_machine_aliases": "broken,laptop=box-2,=empty"})
    assert settings.identity_machine_aliases == {"laptop": "box-2"}


@pytest.mark.asyncio
async def test_member_lookup_failure_records_unknown_and_does_not_raise(monkeypatch):
    calls = 0

    def broken_session():
        nonlocal calls
        calls += 1
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(identity_module, "get_background_session", broken_session)
    local = RequestIdentity("owner-a", "owner-machine", "box-1", "local")

    resolved = await attach_member(local, "member-id-lookup-failure")

    assert resolved == RequestIdentity("unknown", "unknown", "box-1", "local")
    assert await attach_member(local, "member-id-lookup-failure") == resolved
    assert calls == 1
    # A failure is remembered for 10 s, not the 60 s a found name gets; age only the timestamp.
    stored_at, handle, ttl = identity_module._member_names["member-id-lookup-failure"]
    assert (handle, ttl) == (None, 10.0)
    identity_module._member_names["member-id-lookup-failure"] = (stored_at - 11, handle, ttl)
    assert await attach_member(local, "member-id-lookup-failure") == resolved
    assert calls == 2


@pytest.mark.asyncio
async def test_cancelled_member_waiter_does_not_cancel_shared_lookup(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class MemberSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalar(self, statement):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "member-a"

    monkeypatch.setattr(identity_module, "get_background_session", MemberSession)
    caller = RequestIdentity("unknown", "unknown", "box-2", "tailnet")
    first = asyncio.create_task(attach_member(caller, "member-id-cancel"))
    await started.wait()
    second = asyncio.create_task(attach_member(caller, "member-id-cancel"))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == RequestIdentity("member-a", "member", "box-2", "tailnet")
    assert calls == 1


@pytest.mark.asyncio
async def test_member_cache_keeps_only_recordable_handles(monkeypatch):
    class MemberSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalar(self, statement):
            return names.pop(0)

    names = ["Member.B@example.com", "Member.B＠example.com", "Member A"]
    monkeypatch.setattr(identity_module, "get_background_session", MemberSession)
    local = RequestIdentity("owner-a", "owner-machine", "box-1", "local")

    assert await attach_member(local, "member-id-email-cache") == RequestIdentity(
        "unknown", "unknown", "box-1", "local"
    )
    assert await attach_member(local, "member-id-compat-email-cache") == RequestIdentity(
        "unknown", "unknown", "box-1", "local"
    )
    assert await attach_member(local, "member-id-handle-cache") == RequestIdentity(
        "member-a", "member", "box-1", "local"
    )
    assert identity_module._member_names["member-id-email-cache"][1] is None
    assert identity_module._member_names["member-id-compat-email-cache"][1] is None
    assert identity_module._member_names["member-id-handle-cache"][1] == "member-a"
    assert all("@" not in (value or "") for _, value, _ in identity_module._member_names.values())


@pytest.mark.asyncio
async def test_member_lookup_is_bounded_and_shared(monkeypatch):
    calls = 0
    release = asyncio.Event()

    class SlowSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalar(self, statement):
            nonlocal calls
            calls += 1
            await release.wait()
            return "member-a"

    monkeypatch.setattr(identity_module, "get_background_session", SlowSession)
    monkeypatch.setattr(identity_module, "_MEMBER_LOOKUP_TIMEOUT_SECONDS", 0.02)
    local = RequestIdentity("owner-a", "owner-machine", "box-1", "local")
    # Concurrent writes to the same member share the one pending lookup.
    callers = [asyncio.create_task(attach_member(local, "member-id-slow")) for _ in range(10)]
    results = await asyncio.gather(*callers)
    assert calls == 1
    assert results == [RequestIdentity("unknown", "unknown", "box-1", "local")] * 10
    # A timeout is negatively cached while the database is degraded.
    assert await attach_member(local, "member-id-slow") == results[0]
    assert calls == 1
    stored_at, handle, ttl = identity_module._member_names["member-id-slow"]
    assert (handle, ttl) == (None, 10.0)
    identity_module._member_names["member-id-slow"] = (stored_at - 11, handle, ttl)
    release.set()
    assert await attach_member(local, "member-id-slow") == RequestIdentity("member-a", "member", "box-1", "local")
    assert calls == 2


@pytest.mark.asyncio
async def test_member_cache_clears_when_full(monkeypatch):
    class MemberSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalar(self, statement):
            return "member-a"

    monkeypatch.setattr(identity_module, "get_background_session", MemberSession)
    identity_module._member_names.clear()
    identity_module._member_names.update({f"member-{index}": (0.0, "member-b", 60.0) for index in range(512)})
    local = RequestIdentity("unknown", "unknown", "box-1", "local")
    assert await attach_member(local, "new-member") == RequestIdentity("member-a", "member", "box-1", "local")
    assert list(identity_module._member_names) == ["new-member"]
