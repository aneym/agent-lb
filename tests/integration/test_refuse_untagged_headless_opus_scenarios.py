"""Headless Opus must say which seat it is (p6, 2026-10-01 b-20261001231808-112a).

Claude-week rule (a): no new Opus fold runners, helper seats or Claude evals. 94 headless
sessions ran with no seat tag, so nobody could tell which launcher started them. With
AGENT_LB_REFUSE_UNTAGGED_HEADLESS_OPUS on, a headless `claude -p` asking for Opus without an
x-agent-lb-seat header is refused with an error that names the rule and the fix. Interactive
tabs, tagged launches, other models and the default (flag off) are untouched.

With the flag off, agent-lb still logs `untagged_headless_opus_would_refuse` with the client
session id for each request it would refuse, so the flip can be judged on real traffic first.
"""

from __future__ import annotations

import logging

import pytest

from app.core.config.settings import get_settings

pytestmark = pytest.mark.integration

_HEADLESS = "claude-cli/2.1.280 (external, sdk-cli)"
_INTERACTIVE = "claude-cli/2.1.280 (external, cli)"
_WOULD_REFUSE = "untagged_headless_opus_would_refuse"


@pytest.fixture(autouse=True)
def _fresh_settings():
    yield
    get_settings.cache_clear()


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool | None) -> None:
    if on is None:
        monkeypatch.delenv("AGENT_LB_REFUSE_UNTAGGED_HEADLESS_OPUS", raising=False)
    else:
        monkeypatch.setenv("AGENT_LB_REFUSE_UNTAGGED_HEADLESS_OPUS", "true" if on else "false")
    get_settings.cache_clear()


async def _send(async_client, *, model: str, user_agent: str, seat: str | None = None, session: str | None = None):
    headers = {"anthropic-beta": "oauth-2025-04-20", "user-agent": user_agent}
    if session is not None:
        headers["x-claude-code-session-id"] = session
    if seat is not None:
        headers["x-agent-lb-seat"] = seat
    return await async_client.post(
        "/v1/messages",
        json={"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )


def _refused(response) -> bool:
    return response.status_code == 403 and "untagged headless Opus" in response.text


@pytest.mark.asyncio
async def test_untagged_headless_opus_is_refused_with_the_rule_and_the_fix(async_client, monkeypatch):
    _flag(monkeypatch, True)

    response = await _send(async_client, model="claude-opus-5-5", user_agent=_HEADLESS)

    assert response.status_code == 403
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "permission_error"
    message = body["error"]["message"]
    assert "untagged headless Opus" in message
    assert "claude-week rule (a)" in message
    assert "x-agent-lb-seat" in message


@pytest.mark.asyncio
async def test_tagged_interactive_other_models_and_flag_off_pass_through(async_client, monkeypatch):
    _flag(monkeypatch, True)
    tagged = await _send(async_client, model="claude-opus-5-5", user_agent=_HEADLESS, seat="fold:test")
    interactive = await _send(async_client, model="claude-opus-5-5", user_agent=_INTERACTIVE)
    sonnet = await _send(async_client, model="claude-sonnet-5-5", user_agent=_HEADLESS)
    blank_seat = await _send(async_client, model="claude-opus-5-5", user_agent=_HEADLESS, seat="  ")
    for response in (tagged, interactive, sonnet):
        assert not _refused(response), response.text
    assert _refused(blank_seat), blank_seat.text

    for flag in (None, False):
        _flag(monkeypatch, flag)
        response = await _send(async_client, model="claude-opus-5-5", user_agent=_HEADLESS)
        assert not _refused(response), response.text


@pytest.mark.asyncio
async def test_flag_off_logs_what_it_would_refuse_with_the_session(async_client, monkeypatch, caplog):
    _flag(monkeypatch, None)
    caplog.set_level(logging.INFO, logger="app.modules.proxy.api")

    untagged = await _send(async_client, model="claude-opus-5-5", user_agent=_HEADLESS, session="sess-untagged-1")
    await _send(async_client, model="claude-opus-5-5", user_agent=_HEADLESS, seat="fold:test", session="sess-tagged-1")
    await _send(async_client, model="claude-opus-5-5", user_agent=_INTERACTIVE, session="sess-tab-1")
    await _send(async_client, model="claude-sonnet-5-5", user_agent=_HEADLESS, session="sess-sonnet-1")

    assert not _refused(untagged), untagged.text
    would = [r.getMessage() for r in caplog.records if _WOULD_REFUSE in r.getMessage()]
    assert len(would) == 1, would
    assert "sess-untagged-1" in would[0]
