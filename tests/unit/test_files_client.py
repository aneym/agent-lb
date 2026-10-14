

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast

import aiohttp
import pytest

import app.core.clients.files as files_module
from app.core.clients.files import (
    OPENAI_FILE_UPLOAD_LIMIT_BYTES,
    OPENAI_FILE_USE_CASE,
    FileProxyError,
    create_file,
    finalize_file,
)

pytestmark = pytest.mark.unit


class _FakeResponse:


    def __init__(self, *, status: int, body: str) -> None:
        self.status = status
        self._body = body
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    async def text(self) -> str:
        return self._body


class _FakeSession:


    def __init__(self, responses: Sequence[_FakeResponse | Exception]) -> None:
        self._responses: list[_FakeResponse | Exception] = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, data: bytes | str | None, headers: dict[str, str], timeout: object) -> _FakeResponse:
        if not self._responses:
            raise AssertionError(f"_FakeSession exhausted on POST {url}")
        next_value = self._responses.pop(0)
        self.calls.append({"url": url, "data": data, "headers": dict(headers), "timeout": timeout})
        if isinstance(next_value, Exception):
            raise next_value
        return next_value


def _client_session(session: _FakeSession) -> aiohttp.ClientSession:
    return cast(aiohttp.ClientSession, session)


@pytest.mark.asyncio
async def test_create_file_returns_upstream_json_on_success() -> None:
    response_body = json.dumps({"file_id": "file_abc", "upload_url": "https://blob.example/sas?token=xyz"})
    session = _FakeSession([_FakeResponse(status=200, body=response_body)])

    result = await create_file(
        payload={"file_name": "page.pdf", "file_size": 1024, "use_case": OPENAI_FILE_USE_CASE},
        headers={"User-Agent": "codex-cli/1.0", "x-codex-version": "1.2.3", "Authorization": "Bearer not-forwarded"},
        access_token="upstream-token",
        account_id="acc_1",
        session=_client_session(session),
        allow_direct_egress=True,
    )

    assert result == {"file_id": "file_abc", "upload_url": "https://blob.example/sas?token=xyz"}
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"].endswith("/files")
    sent_headers = call["headers"]
    assert sent_headers["Authorization"] == "Bearer upstream-token"
    assert sent_headers["chatgpt-account-id"] == "acc_1"

    assert sent_headers["User-Agent"] == "codex-cli/1.0"
    assert sent_headers["x-codex-version"] == "1.2.3"
    body = json.loads(call["data"])
    assert body == {"file_name": "page.pdf", "file_size": 1024, "use_case": "codex"}


@pytest.mark.asyncio
async def test_create_file_maps_error_status_to_proxy_error() -> None:
    error_body = json.dumps({"error": {"message": "file too large", "type": "invalid_request_error"}})
    session = _FakeSession([_FakeResponse(status=413, body=error_body)])

    with pytest.raises(FileProxyError) as info:
        await create_file(
            payload={"file_name": "big.bin", "file_size": OPENAI_FILE_UPLOAD_LIMIT_BYTES, "use_case": "codex"},
            headers={},
            access_token="t",
            account_id=None,
            session=_client_session(session),
            allow_direct_egress=True,
        )
    assert info.value.status_code == 413
    assert info.value.payload == {"error": {"message": "file too large", "type": "invalid_request_error"}}


@pytest.mark.asyncio
async def test_create_file_non_json_body_yields_502() -> None:
    session = _FakeSession([_FakeResponse(status=200, body="<html>not json</html>")])

    with pytest.raises(FileProxyError) as info:
        await create_file(
            payload={"file_name": "x.png", "file_size": 1, "use_case": "codex"},
            headers={},
            access_token="t",
            account_id=None,
            session=_client_session(session),
            allow_direct_egress=True,
        )
    assert info.value.status_code == 502
    assert info.value.payload["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_create_file_transport_failure_yields_502() -> None:
    session = _FakeSession([aiohttp.ClientConnectionError("connection reset")])

    with pytest.raises(FileProxyError) as info:
        await create_file(
            payload={"file_name": "x.png", "file_size": 1, "use_case": "codex"},
            headers={},
            access_token="t",
            account_id=None,
            session=_client_session(session),
            allow_direct_egress=True,
        )
    assert info.value.status_code == 502
    assert info.value.payload["error"]["code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_create_file_leases_shared_session_when_session_not_supplied(monkeypatch: pytest.MonkeyPatch) -> None:
    response_body = json.dumps({"file_id": "file_abc", "upload_url": "https://blob.example/sas"})
    session = _FakeSession([_FakeResponse(status=200, body=response_body)])
    events: list[str] = []

    @asynccontextmanager
    async def _lease_session(session_arg: aiohttp.ClientSession | None) -> AsyncIterator[Any]:
        assert session_arg is None
        events.append("enter")
        try:
            yield session
        finally:
            events.append(f"exit:{len(session.calls)}")

    monkeypatch.setattr(files_module, "lease_http_session", _lease_session)

    result = await create_file(
        payload={"file_name": "page.pdf", "file_size": 1024, "use_case": OPENAI_FILE_USE_CASE},
        headers={},
        access_token="upstream-token",
        account_id="acc_1",
        allow_direct_egress=True,
    )

    assert result["file_id"] == "file_abc"
    assert events == ["enter", "exit:1"]


@pytest.mark.asyncio
async def test_finalize_file_returns_immediately_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(
        {
            "status": "success",
            "download_url": "https://download.example/file_abc",
            "file_name": "page.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 1024,
        }
    )
    session = _FakeSession([_FakeResponse(status=200, body=body)])
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(files_module.asyncio, "sleep", _record_sleep)

    result = await finalize_file(
        file_id="file_abc",
        headers={},
        access_token="t",
        account_id="acc_1",
        session=_client_session(session),
        allow_direct_egress=True,
    )

    assert result["status"] == "success"
    assert result["download_url"] == "https://download.example/file_abc"
    assert sleeps == [], "should not sleep when first poll already succeeded"
    assert len(session.calls) == 1
    assert session.calls[0]["url"].endswith("/files/file_abc/uploaded")
    assert session.calls[0]["data"] == b"{}"


@pytest.mark.asyncio
async def test_finalize_file_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    retry_body = json.dumps({"status": "retry"})
    success_body = json.dumps({"status": "success", "download_url": "https://download.example/f"})
    session = _FakeSession(
        [
            _FakeResponse(status=200, body=retry_body),
            _FakeResponse(status=200, body=retry_body),
            _FakeResponse(status=200, body=success_body),
        ]
    )
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(files_module.asyncio, "sleep", _record_sleep)

    result = await finalize_file(
        file_id="f",
        headers={},
        access_token="t",
        account_id=None,
        session=_client_session(session),
        allow_direct_egress=True,
    )

    assert result["status"] == "success"
    assert len(session.calls) == 3


    assert sleeps == [files_module._FILE_FINALIZE_POLL_DELAY_SECONDS] * 2


@pytest.mark.asyncio
async def test_finalize_file_holds_shared_session_lease_across_poll_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    retry_body = json.dumps({"status": "retry"})
    success_body = json.dumps({"status": "success", "download_url": "https://download.example/f"})
    session = _FakeSession(
        [
            _FakeResponse(status=200, body=retry_body),
            _FakeResponse(status=200, body=success_body),
        ]
    )
    events: list[str] = []

    async def _record_sleep(seconds: float) -> None:
        return None

    @asynccontextmanager
    async def _lease_session(session_arg: aiohttp.ClientSession | None) -> AsyncIterator[Any]:
        assert session_arg is None
        events.append("enter")
        try:
            yield session
        finally:
            events.append(f"exit:{len(session.calls)}")

    monkeypatch.setattr(files_module.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(files_module, "lease_http_session", _lease_session)

    result = await finalize_file(
        file_id="f",
        headers={},
        access_token="t",
        account_id=None,
        allow_direct_egress=True,
    )

    assert result["status"] == "success"
    assert len(session.calls) == 2
    assert events == ["enter", "exit:2"]


@pytest.mark.asyncio
async def test_finalize_file_returns_last_retry_after_budget_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    retry_body = json.dumps({"status": "retry"})


    session = _FakeSession([_FakeResponse(status=200, body=retry_body) for _ in range(8)])

    fake_now = [0.0]

    def _monotonic() -> float:
        return fake_now[0]

    async def _advance(seconds: float) -> None:


        fake_now[0] += 60.0

    monkeypatch.setattr(files_module.time, "monotonic", _monotonic)
    monkeypatch.setattr(files_module.asyncio, "sleep", _advance)

    result = await finalize_file(
        file_id="f",
        headers={},
        access_token="t",
        account_id=None,
        session=_client_session(session),
        allow_direct_egress=True,
    )

    assert result == {"status": "retry"}





    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_finalize_file_maps_error_status_to_proxy_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error_body = json.dumps({"error": {"message": "not found", "type": "invalid_request_error"}})
    session = _FakeSession([_FakeResponse(status=404, body=error_body)])

    async def _no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(files_module.asyncio, "sleep", _no_sleep)

    with pytest.raises(FileProxyError) as info:
        await finalize_file(
            file_id="missing",
            headers={},
            access_token="t",
            account_id=None,
            session=_client_session(session),
            allow_direct_egress=True,
        )
    assert info.value.status_code == 404
    assert info.value.payload == {"error": {"message": "not found", "type": "invalid_request_error"}}


@pytest.mark.asyncio
async def test_finalize_file_transport_timeout_yields_502(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession([asyncio.TimeoutError()])

    async def _no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(files_module.asyncio, "sleep", _no_sleep)

    with pytest.raises(FileProxyError) as info:
        await finalize_file(
            file_id="f",
            headers={},
            access_token="t",
            account_id=None,
            session=_client_session(session),
            allow_direct_egress=True,
        )
    assert info.value.status_code == 502
    assert info.value.payload["error"]["code"] == "upstream_unavailable"
