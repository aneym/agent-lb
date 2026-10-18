from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.core.config.settings import get_settings
from tests.integration.federation_push_helpers import _LOCAL_INSTANCE_ID as _LOCAL_INSTANCE_ID
from tests.integration.federation_push_helpers import _NATE_IP as _NATE_IP
from tests.integration.federation_push_helpers import _NATE_LOGIN as _NATE_LOGIN
from tests.integration.federation_push_helpers import _PULL_PEER_INSTANCE_ID as _PULL_PEER_INSTANCE_ID
from tests.integration.federation_push_helpers import _PUSH as _PUSH
from tests.integration.federation_push_helpers import _SENDER_INSTANCE_ID as _SENDER_INSTANCE_ID
from tests.integration.federation_push_helpers import _STRANGER_IP as _STRANGER_IP
from tests.integration.federation_push_helpers import _account as _account
from tests.integration.federation_push_helpers import _bump_mtime as _bump_mtime
from tests.integration.federation_push_helpers import _get_account as _get_account
from tests.integration.federation_push_helpers import _nate_source as _nate_source
from tests.integration.federation_push_helpers import _peer as _peer
from tests.integration.federation_push_helpers import _push as _push
from tests.integration.federation_push_helpers import _push_auth as _push_auth
from tests.integration.federation_push_helpers import _push_body as _push_body
from tests.integration.federation_push_helpers import _push_receiver as _push_receiver
from tests.integration.federation_push_helpers import _seed_account as _seed_account
from tests.integration.federation_push_helpers import _snapshot_accounts as _snapshot_accounts
from tests.integration.federation_push_helpers import _whois_count as _whois_count
from tests.integration.federation_push_helpers import _write_sources as _write_sources
from tests.integration.federation_push_helpers import fake_tailscale as fake_tailscale
from tests.integration.federation_push_helpers import sources_path as sources_path
from tests.integration.federation_push_helpers import state_path as state_path
from tests.integration.federation_push_helpers import whois_calls as whois_calls

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_listed_login_may_push_case_insensitively(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source()])

    response = await _push(app_instance, _push_body(_account("x")))

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "nate"
    assert response.json()["accepted"] == ["x"]


@pytest.mark.asyncio
async def test_callers_the_receiver_cannot_place_are_refused(
    async_client, app_instance, sources_path: Path, whois_calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    table, calls = whois_calls
    _write_sources(sources_path, [_nate_source()])
    before = await _snapshot_accounts()

    table["127.0.0.1"] = _NATE_LOGIN
    loopback = await async_client.post(_PUSH, json=_push_body(_account("x")))
    assert loopback.status_code == 403

    stranger = await _push(app_instance, _push_body(_account("x")), ip=_STRANGER_IP)
    assert stranger.status_code == 403

    unknown = await _push(app_instance, _push_body(_account("x")), ip="100.64.0.99")
    assert unknown.status_code == 403

    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    get_settings.cache_clear()
    unresolved = await async_client.post(
        _PUSH, json=_push_body(_account("x")), headers={"X-Forwarded-For": "not-an-ip"}
    )
    assert unresolved.status_code == 403

    assert "127.0.0.1" not in calls
    assert await _snapshot_accounts() == before


@pytest.mark.asyncio
async def test_identity_headers_are_ignored(async_client, app_instance, sources_path: Path, whois_calls) -> None:
    _write_sources(sources_path, [_nate_source()])

    response = await _push(
        app_instance,
        _push_body(_account("x")),
        ip=_STRANGER_IP,
        headers={"Tailscale-User-Login": _NATE_LOGIN, "Tailscale-User-Name": "Nate"},
    )

    assert response.status_code == 403
    assert await _get_account("x") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [None, "{not json"], ids=["missing", "malformed"])
async def test_missing_or_malformed_sources_file_refuses_everyone(
    async_client, app_instance, sources_path: Path, whois_calls, contents: str | None
) -> None:
    if contents is not None:
        sources_path.write_text(contents)

    response = await _push(app_instance, _push_body(_account("x")))

    assert response.status_code == 403
    assert await _get_account("x") is None


@pytest.mark.asyncio
async def test_real_client_ip_behind_tailscale_serve_is_used(
    async_client, app_instance, sources_path: Path, whois_calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, calls = whois_calls
    _write_sources(sources_path, [_nate_source()])
    forwarded = {"X-Forwarded-For": _NATE_IP}

    untrusted = await _push(app_instance, _push_body(_account("x")), ip="127.0.0.1", headers=forwarded)
    assert untrusted.status_code == 403
    assert calls == []

    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    get_settings.cache_clear()
    trusted = await _push(app_instance, _push_body(_account("x")), ip="127.0.0.1", headers=forwarded)

    assert trusted.status_code == 200, trusted.text
    assert calls == [_NATE_IP]


@pytest.mark.asyncio
async def test_team_mode_and_proxy_keys_do_not_gate_the_push(
    async_client, app_instance, sources_path: Path, whois_calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_MODE", "disabled")
    get_settings.cache_clear()
    enabled = await async_client.put("/api/settings", json={"teamModeEnabled": True, "apiKeyAuthEnabled": True})
    assert enabled.status_code == 200, enabled.text
    _write_sources(sources_path, [_nate_source()])

    async with _peer(app_instance, _NATE_IP) as peer:
        gated = await peer.get("/api/accounts")
        assert gated.status_code in (401, 403), gated.text
        pushed = await peer.post(_PUSH, json=_push_body(_account("x")))

    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["accepted"] == ["x"]


@pytest.mark.asyncio
async def test_sources_file_edits_apply_without_restart(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [{"name": "someone", "tailscale_logins": ["someone@example.com"]}])
    refused = await _push(app_instance, _push_body(_account("x")))
    assert refused.status_code == 403

    _write_sources(sources_path, [_nate_source()])
    _bump_mtime(sources_path)
    accepted = await _push(app_instance, _push_body(_account("x")))

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["source"] == "nate"


@pytest.mark.asyncio
async def test_first_push_binds_source_with_private_state_file(
    async_client, app_instance, sources_path: Path, state_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source()])

    response = await _push(app_instance, _push_body(_account("x")))

    assert response.status_code == 200, response.text
    assert _push_receiver().load_binding(state_path) == {"nate": _SENDER_INSTANCE_ID}
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_second_instance_cannot_take_over_bound_source(
    async_client, app_instance, sources_path: Path, state_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source()])
    first = await _push(app_instance, _push_body(_account("x"), _account("y")))
    assert first.status_code == 200, first.text
    before = await _snapshot_accounts()

    takeover = await _push(app_instance, _push_body(_account("z"), instance_id="other-lb"))

    assert takeover.status_code == 409
    assert await _snapshot_accounts() == before
    assert _push_receiver().load_binding(state_path) == {"nate": _SENDER_INSTANCE_ID}


@pytest.mark.asyncio
async def test_push_cannot_claim_own_or_pull_peer_instance_id(
    async_client, app_instance, sources_path: Path, state_path: Path, whois_calls
) -> None:
    await _seed_account("z", owner_instance=_PULL_PEER_INSTANCE_ID, access_token="laptop-access")
    await _seed_account("o", owner_instance=_LOCAL_INSTANCE_ID, access_token="local-access")
    _write_sources(sources_path, [_nate_source()])
    before = await _snapshot_accounts()

    as_local = await _push(app_instance, _push_body(_account("x"), instance_id=_LOCAL_INSTANCE_ID))

    as_pull_peer = await _push(app_instance, _push_body(instance_id=_PULL_PEER_INSTANCE_ID))

    assert as_local.status_code == 409
    assert as_pull_peer.status_code == 409
    assert await _snapshot_accounts() == before
    assert _push_receiver().load_binding(state_path) == {}


@pytest.mark.asyncio
async def test_whois_parses_json_after_a_leading_warning_line_and_caches_per_ip(fake_tailscale) -> None:
    log, _ = fake_tailscale
    push_auth = _push_auth()

    first = await push_auth.resolve_tailscale_login("100.64.77.201")
    second = await push_auth.resolve_tailscale_login("100.64.77.201")

    assert first == "Nate@Example.com"
    assert second == "Nate@Example.com"
    assert _whois_count(log, "100.64.77.201") == 1


@pytest.mark.asyncio
async def test_whois_failures_return_none_and_are_not_cached(fake_tailscale) -> None:
    log, healed = fake_tailscale
    push_auth = _push_auth()

    assert await push_auth.resolve_tailscale_login("100.64.77.203") is None
    assert await push_auth.resolve_tailscale_login("100.64.77.202") is None
    healed.touch()
    assert await push_auth.resolve_tailscale_login("100.64.77.202") == "Nate@Example.com"
    assert _whois_count(log, "100.64.77.202") == 2


def test_sources_file_shape_and_case_insensitive_match(tmp_path: Path) -> None:
    push_auth = _push_auth()
    path = tmp_path / "federation-push-sources.json"
    assert push_auth.load_push_sources(path) == []

    _write_sources(path, [{"name": "nate", "tailscale_logins": ["Nate@Example.com", "n2@example.com"]}])
    sources = push_auth.load_push_sources(path)

    assert sources == [
        push_auth.PushSource(name="nate", tailscale_logins=("Nate@Example.com", "n2@example.com"), max_accounts=25)
    ]
    matched = push_auth.match_source("NATE@example.COM", sources)
    assert matched is not None and matched.name == "nate"
    assert push_auth.match_source("other@example.com", sources) is None
    assert push_auth.match_source(None, sources) is None
