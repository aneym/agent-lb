"""Request logs carry who and where through the real HTTP stack and every U2 writer."""

from __future__ import annotations

import ipaddress
from uuid import uuid4

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.config.settings import get_settings
from app.core.identity import RequestIdentity, reset_request_identity, set_request_identity
from app.core.utils.time import utcnow
from app.db.models import RequestLog, TeamMember
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from app.modules.request_logs.repository import RequestLogsRepository
from tests.integration.test_anthropic_proxy import (
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration

TAILNET_PEER = str(ipaddress.ip_network("100.64.0.0/10")[11])


@pytest.fixture
def identity_env(monkeypatch):
    monkeypatch.setenv("AGENT_LB_IDENTITY_OWNER", "owner-a")
    monkeypatch.setenv("AGENT_LB_IDENTITY_OWNER_MACHINES", "box-1")
    monkeypatch.setenv("AGENT_LB_IDENTITY_LOCAL_MACHINE", "box-1")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _create_key(*, name: str, member_name: str | None = None) -> str:
    async with SessionLocal() as session:
        member_id = None
        if member_name is not None:
            # Unique per test: the member-name cache outlives a test's database.
            member_id = f"member-{uuid4().hex}"
            session.add(TeamMember(id=member_id, name=member_name, created_at=utcnow(), updated_at=utcnow()))
            await session.commit()
        created = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(name=name, allowed_models=None, expires_at=None, member_id=member_id)
        )
    return created.key


def _caller(row: RequestLog) -> tuple[str | None, str | None, str | None, str | None]:
    return row.caller_user, row.caller_user_source, row.caller_machine, row.caller_machine_source


@pytest.mark.asyncio
async def test_proxy_request_logs_carry_caller_identity(identity_env, async_client):
    member_key = await _create_key(name="key-label-1", member_name="Member A")
    named_key = await _create_key(name="key-label-2")
    cases = {
        "identity-keyless-local": ({}, ("owner-a", "owner-machine", "box-1", "local")),
        "identity-member-key": (
            {"authorization": f"Bearer {member_key}"},
            ("member-a", "member", "box-1", "local"),
        ),
        # A key's own name is never a person: this key has no member, so the owner rule applies.
        "identity-named-key": (
            {"authorization": f"Bearer {named_key}"},
            ("owner-a", "owner-machine", "box-1", "local"),
        ),
        # Serve-shaped: the tailnet peer is not an owner machine and is not in the node cache.
        "identity-keyless-tailnet": (
            {"x-forwarded-for": TAILNET_PEER, "x-forwarded-proto": "https"},
            ("unknown", "unknown", "tailnet-unknown", "tailnet"),
        ),
    }

    for request_id, (headers, _) in cases.items():
        response = await async_client.post(
            "/backend-api/codex/responses",
            json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
            headers={"x-request-id": request_id, **headers},
        )
        assert response.status_code == 200

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.request_id.in_(list(cases))))
        rows = {row.request_id: _caller(row) for row in result.scalars()}
    assert rows == {request_id: expected for request_id, (_, expected) in cases.items()}


@pytest.mark.asyncio
async def test_anthropic_request_log_names_the_member(identity_env, async_client, monkeypatch):
    member_key = await _create_key(name="key-label-3", member_name="member-c")
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20", "x-api-key": member_key},
    ) as response:
        assert response.status_code == 200
        await response.aread()

    async with SessionLocal() as session:
        rows = list((await session.execute(select(RequestLog))).scalars())
    assert [(row.provider, _caller(row)) for row in rows] == [
        ("anthropic", ("member-c", "member", "box-1", "local")),
    ]


@pytest.mark.parametrize(
    ("enabled", "explicit", "expected"),
    [
        pytest.param("true", None, ("internal", "internal", "box-1", "internal"), id="no-request-context"),
        pytest.param(
            "true",
            RequestIdentity("member-a", "member", "box-2", "tailnet"),
            ("member-a", "member", "box-2", "tailnet"),
            id="explicit-identity",
        ),
        pytest.param(
            "false",
            RequestIdentity("member-a", "member", "box-2", "tailnet"),
            (None, None, None, None),
            id="disabled-stores-nothing",
        ),
    ],
)
@pytest.mark.asyncio
async def test_add_log_identity_outside_a_request(db_setup, identity_env, monkeypatch, enabled, explicit, expected):
    del db_setup, identity_env
    monkeypatch.setenv("AGENT_LB_IDENTITY_ENABLED", enabled)
    get_settings.cache_clear()

    async with SessionLocal() as session:
        await RequestLogsRepository(session).add_log(
            account_id=None,
            request_id="identity-outside-request",
            model="example-model",
            input_tokens=None,
            output_tokens=None,
            latency_ms=None,
            status="error",
            error_code=None,
            identity=explicit,
        )
        await session.commit()
        row = (
            await session.execute(select(RequestLog).where(RequestLog.request_id == "identity-outside-request"))
        ).scalar_one()
    assert _caller(row) == expected


@pytest.mark.asyncio
async def test_add_log_without_explicit_identity_uses_request_context(db_setup, identity_env):
    del db_setup, identity_env
    caller = RequestIdentity("member-a", "member", "box-2", "tailnet")
    token = set_request_identity(caller)
    try:
        async with SessionLocal() as session:
            await RequestLogsRepository(session).add_log(
                account_id=None,
                request_id="identity-context-request",
                model="example-model",
                input_tokens=None,
                output_tokens=None,
                latency_ms=None,
                status="error",
                error_code=None,
            )
            await session.commit()
            row = (
                await session.execute(select(RequestLog).where(RequestLog.request_id == "identity-context-request"))
            ).scalar_one()
        assert _caller(row) == ("member-a", "member", "box-2", "tailnet")
    finally:
        reset_request_identity(token)
