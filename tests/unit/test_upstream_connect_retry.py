import aiohttp
import pytest

from app.modules.proxy import anthropic_service as svc


class _Ctx:
    def __init__(self, error=None, value="resp"):
        self.error, self.value, self.exited = error, value, False

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self.value

    async def __aexit__(self, *exc):
        self.exited = True
        return None


@pytest.mark.asyncio
async def test_connect_retry_recovers_after_transient_connect_errors(monkeypatch):
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(svc, "_CONNECT_RETRY_SLEEP", fake_sleep)
    contexts = [
        _Ctx(aiohttp.ServerTimeoutError("Connection timeout to host x")),
        _Ctx(aiohttp.ClientOSError(54, "reset")),
        _Ctx(value="ok"),
    ]
    it = iter(contexts)
    wrapper = svc._ConnectRetryingResponse(lambda: next(it), attempts=3, label="Anthropic")
    async with wrapper as resp:
        assert resp == "ok"
    assert sleeps == [pytest.approx(0.5), pytest.approx(1.0)]
    assert contexts[2].exited is True


@pytest.mark.asyncio
async def test_connect_retry_exhaustion_is_a_503_proxy_error(monkeypatch):
    async def fake_sleep(delay):
        pass

    monkeypatch.setattr(svc, "_CONNECT_RETRY_SLEEP", fake_sleep)
    opened = []

    def open_ctx():
        opened.append(1)
        return _Ctx(aiohttp.ServerTimeoutError("Connection timeout to host x"))

    wrapper = svc._ConnectRetryingResponse(open_ctx, attempts=3, label="Anthropic")
    with pytest.raises(svc.AnthropicProxyError) as excinfo:
        async with wrapper:
            pass
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "upstream_unreachable"
    assert len(opened) == 3


@pytest.mark.asyncio
async def test_connect_retry_does_not_retry_after_headers_arrive(monkeypatch):
    ctx = _Ctx(value="ok")
    wrapper = svc._ConnectRetryingResponse(lambda: ctx, attempts=3, label="Anthropic")
    with pytest.raises(RuntimeError):
        async with wrapper:
            raise RuntimeError("mid-stream failure belongs to the caller")
    assert ctx.exited is True


def test_connect_backoff_is_bounded():
    assert [svc._connect_backoff_seconds(i) for i in range(5)] == [0.5, 1.0, 2.0, 4.0, 4.0]
