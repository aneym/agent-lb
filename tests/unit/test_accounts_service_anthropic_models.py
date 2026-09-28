from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts import service as service_module
from app.modules.accounts.service import ANTHROPIC_MODELS_TTL_SECONDS, AccountsService

pytestmark = pytest.mark.unit

_MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-sonnet-5"]


def _account(account_id: str, provider: str, status: AccountStatus) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=None,
        email=f"{account_id}@example.com",
        plan_type="max",
        provider=provider,
        access_token_encrypted=encryptor.encrypt(f"token-{account_id}"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime(2026, 9, 28),
        status=status,
        deactivation_reason=None,
    )


def _service(accounts: list[Account]) -> AccountsService:
    repo = AsyncMock()
    repo.list_accounts.return_value = accounts
    return AccountsService(repo=repo)


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "_anthropic_models", None)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(service_module.time, "monotonic", lambda: now[0])
    return now


@pytest.mark.asyncio
async def test_lists_through_an_anthropic_account_and_caches_for_the_ttl(monkeypatch, clock):
    calls: list[str] = []

    async def upstream(*, access_token: str, base_url: str) -> tuple[int, list[str]]:
        calls.append(access_token)
        return 200, list(_MODELS)

    monkeypatch.setattr(service_module.probes, "list_anthropic_models", upstream)
    service = _service([
        _account("openai-1", "openai", AccountStatus.ACTIVE),
        _account("paused", "anthropic", AccountStatus.PAUSED),
        _account("limited", "anthropic", AccountStatus.RATE_LIMITED),
        _account("active", "anthropic", AccountStatus.ACTIVE),
    ])

    first = await service.list_anthropic_models()
    clock[0] += ANTHROPIC_MODELS_TTL_SECONDS - 1
    second = await service.list_anthropic_models()

    assert first.models == second.models == _MODELS
    assert not first.stale and first.error is None
    # The active Anthropic account is used first; OpenAI and paused accounts never are; the cache holds for the TTL.
    assert calls == ["token-active"]


@pytest.mark.asyncio
async def test_a_refused_account_moves_to_the_next(monkeypatch, clock):
    calls: list[str] = []

    async def upstream(*, access_token: str, base_url: str) -> tuple[int, list[str]]:
        calls.append(access_token)
        return (429, []) if access_token == "token-a" else (200, list(_MODELS))

    monkeypatch.setattr(service_module.probes, "list_anthropic_models", upstream)
    service = _service([
        _account("a", "anthropic", AccountStatus.ACTIVE),
        _account("b", "anthropic", AccountStatus.QUOTA_EXCEEDED),
    ])

    result = await service.list_anthropic_models()

    assert result.models == _MODELS
    assert calls == ["token-a", "token-b"]


@pytest.mark.asyncio
async def test_a_failed_refresh_serves_the_last_good_list_as_stale(monkeypatch, clock):
    answers = [(200, list(_MODELS)), (503, [])]

    async def upstream(*, access_token: str, base_url: str) -> tuple[int, list[str]]:
        return answers.pop(0)

    monkeypatch.setattr(service_module.probes, "list_anthropic_models", upstream)
    service = _service([_account("a", "anthropic", AccountStatus.ACTIVE)])

    await service.list_anthropic_models()
    clock[0] += ANTHROPIC_MODELS_TTL_SECONDS + 1
    stale = await service.list_anthropic_models()

    assert stale.models == _MODELS
    assert stale.stale is True
    assert stale.error == "upstream model list returned HTTP 503"


@pytest.mark.asyncio
async def test_no_listable_account_returns_an_empty_list_with_the_reason(monkeypatch, clock):
    upstream = AsyncMock()
    monkeypatch.setattr(service_module.probes, "list_anthropic_models", upstream)
    service = _service([_account("gone", "anthropic", AccountStatus.DEACTIVATED)])

    result = await service.list_anthropic_models()

    assert result.models == []
    assert result.error == "no Anthropic account can list models"
    upstream.assert_not_called()


@pytest.mark.asyncio
async def test_a_limited_account_lists_without_a_refresh_and_a_paused_one_is_dropped(monkeypatch, clock):
    calls: list[str] = []

    async def upstream(*, access_token: str, base_url: str) -> tuple[int, list[str]]:
        calls.append(access_token)
        return 200, list(_MODELS)

    monkeypatch.setattr(service_module.probes, "list_anthropic_models", upstream)
    active = _account("a", "anthropic", AccountStatus.ACTIVE)
    limited = _account("b", "anthropic", AccountStatus.QUOTA_EXCEEDED)
    service = _service([active, limited])
    paused_meanwhile = _account("a", "anthropic", AccountStatus.PAUSED)
    auth = AsyncMock()
    auth.ensure_fresh.return_value = paused_meanwhile
    service._auth_manager = auth

    result = await service.list_anthropic_models()

    assert result.models == _MODELS
    # The active account came back paused from its refresh, so it is never used; the limited one is never refreshed.
    assert calls == ["token-b"]
    auth.ensure_fresh.assert_awaited_once_with(active, force=False)


@pytest.mark.asyncio
async def test_concurrent_refreshes_list_upstream_once(monkeypatch, clock):
    import asyncio

    calls: list[str] = []
    release = asyncio.Event()

    async def upstream(*, access_token: str, base_url: str) -> tuple[int, list[str]]:
        calls.append(access_token)
        await release.wait()
        return 200, list(_MODELS)

    monkeypatch.setattr(service_module.probes, "list_anthropic_models", upstream)
    service = _service([_account("a", "anthropic", AccountStatus.ACTIVE)])

    first = asyncio.create_task(service.list_anthropic_models())
    second = asyncio.create_task(service.list_anthropic_models())
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)

    assert [result.models for result in results] == [_MODELS, _MODELS]
    assert calls == ["token-a"]


def test_the_refresh_lock_follows_the_running_loop():
    import asyncio

    async def contend() -> None:
        lock = service_module._anthropic_models_refresh_lock()
        async with lock:
            waiter = asyncio.create_task(lock.acquire())
            await asyncio.sleep(0)
        await waiter
        lock.release()

    # A lock that a waiter bound to the first loop must not be reused on the next one.
    asyncio.run(contend())
    asyncio.run(contend())

    async def held_across_another_loop() -> None:
        lock = service_module._anthropic_models_refresh_lock()
        async with lock:
            async def ask() -> asyncio.Lock:
                return service_module._anthropic_models_refresh_lock()

            other = await asyncio.to_thread(asyncio.run, ask())
            assert other is not lock
            # Loop A still gets its own held lock after loop B asked for one.
            assert service_module._anthropic_models_refresh_lock() is lock
            assert lock.locked()

    asyncio.run(held_across_another_loop())


class _JsonResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: str | None = None) -> object:
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Lease:
    def __init__(self, response: _JsonResponse, captured: dict[str, object]) -> None:
        self._response, self._captured = response, captured

    async def __aenter__(self):
        response, captured = self._response, self._captured

        class _Session:
            def get(self, url: str, **kwargs: object):
                captured.update(url=url, **kwargs)
                return response

        return _Session()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_upstream_list_sends_the_oauth_headers_and_returns_ids(monkeypatch):
    from app.modules.accounts import probes

    captured: dict[str, object] = {}
    payload = {"data": [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5", "type": "model"}]}
    monkeypatch.setattr(probes, "lease_http_session", lambda: _Lease(_JsonResponse(200, payload), captured))

    status, ids = await probes.list_anthropic_models(access_token="token-x", base_url="https://api.example.test")

    assert (status, ids) == (200, ["claude-sonnet-5-5"])
    assert captured["url"] == "https://api.example.test/v1/models?limit=1000"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer token-x"
    assert headers["anthropic-beta"] == probes.ANTHROPIC_OAUTH_BETA


@pytest.mark.asyncio
async def test_upstream_list_error_returns_the_status_and_no_ids(monkeypatch):
    from app.modules.accounts import probes

    monkeypatch.setattr(probes, "lease_http_session", lambda: _Lease(_JsonResponse(429, {"error": {}}), {}))
    assert await probes.list_anthropic_models(access_token="t", base_url="https://api.example.test") == (429, [])
    monkeypatch.setattr(probes, "lease_http_session", lambda: _Lease(_JsonResponse(200, {"nope": 1}), {}))
    assert await probes.list_anthropic_models(access_token="t", base_url="https://api.example.test") == (0, [])
