from __future__ import annotations

from typing import cast

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.files import FileProxyError
from tests.integration.proxy_files_support import (
    _import_account as _import_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_backend_files_create_forwards_payload_and_returns_upstream_json(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_create", "files-create@example.com")

    captured: dict[str, object] = {}

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        captured["payload"] = payload
        captured["access_token"] = access_token
        captured["account_id"] = account_id
        return {"file_id": "file_xyz", "upload_url": "https://blob.example/sas?token=abc"}

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "page.pdf", "file_size": 1024, "use_case": "codex"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"file_id": "file_xyz", "upload_url": "https://blob.example/sas?token=abc"}
    assert captured["payload"] == {"file_name": "page.pdf", "file_size": 1024, "use_case": "codex"}
    assert captured["access_token"] == "access-token"
    assert captured["account_id"] == "acc_files_create"


@pytest.mark.asyncio
async def test_backend_files_create_defaults_use_case_to_codex(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_default_uc", "files-default-uc@example.com")
    captured: dict[str, object] = {}

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        captured["payload"] = payload
        return {"file_id": "f", "upload_url": "https://blob.example/sas"}

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 1},
    )

    assert response.status_code == 200
    captured_payload = cast(dict[str, object], captured["payload"])
    assert captured_payload["use_case"] == "codex"


@pytest.mark.asyncio
async def test_backend_files_create_rejects_zero_file_size(async_client):
    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 0},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_backend_files_create_rejects_oversized_file(async_client):
    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "huge.bin", "file_size": 512 * 1024 * 1024 + 1},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_backend_files_create_rejects_missing_file_name(async_client):
    response = await async_client.post(
        "/backend-api/files",
        json={"file_size": 100},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_backend_files_create_maps_upstream_error(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_upstream_err", "files-upstream-err@example.com")

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        raise FileProxyError(
            413,
            {"error": {"message": "file too large", "type": "invalid_request_error", "code": "file_too_large"}},
        )

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 1},
    )

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "file_too_large"


@pytest.mark.asyncio
async def test_backend_files_create_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_invalidated_a", "files-invalidated-a@example.com")
    await _import_account(async_client, "acc_files_invalidated_b", "files-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        del payload, headers, access_token, base_url, session
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise FileProxyError(
                401,
                {"error": {"message": "token invalidated", "type": "authentication_error", "code": "invalid_api_key"}},
            )
        return {"file_id": "file_recovered", "upload_url": "https://blob.example/recovered"}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 1},
    )

    assert response.status_code == 200
    assert response.json()["file_id"] == "file_recovered"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_backend_files_finalize_returns_upstream_payload(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_finalize", "files-finalize@example.com")

    captured: dict[str, object] = {}

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        captured["file_id"] = file_id
        captured["account_id"] = account_id
        return {
            "status": "success",
            "download_url": "https://download.example/file_done",
            "file_name": "page.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 1024,
        }

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    response = await async_client.post("/backend-api/files/file_done/uploaded")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["download_url"] == "https://download.example/file_done"
    assert captured["file_id"] == "file_done"
    assert captured["account_id"] == "acc_files_finalize"


@pytest.mark.asyncio
async def test_backend_files_finalize_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_finalize_invalidated_a", "files-finalize-invalidated-a@example.com")
    await _import_account(async_client, "acc_files_finalize_invalidated_b", "files-finalize-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        del file_id, headers, access_token, base_url, session
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise FileProxyError(
                401,
                {"error": {"message": "token invalidated", "type": "authentication_error", "code": "invalid_api_key"}},
            )
        return {"status": "success", "download_url": "https://download.example/recovered"}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post("/backend-api/files/file_recovered/uploaded")

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_backend_files_finalize_propagates_retry_status(async_client, monkeypatch):

    await _import_account(async_client, "acc_files_finalize_retry", "files-finalize-retry@example.com")

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        return {"status": "retry"}

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    response = await async_client.post("/backend-api/files/file_pending/uploaded")
    assert response.status_code == 200
    assert response.json() == {"status": "retry"}


@pytest.mark.asyncio
async def test_backend_files_finalize_maps_upstream_404(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_finalize_missing", "files-finalize-missing@example.com")

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        raise FileProxyError(
            404,
            {"error": {"message": "file not found", "type": "invalid_request_error", "code": "not_found"}},
        )

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    response = await async_client.post("/backend-api/files/missing_id/uploaded")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,method,payload",
    [
        ("/backend-api/files", "post", {"file_name": "x.png", "file_size": 1}),
        ("/backend-api/files/file_x/uploaded", "post", None),
    ],
)
async def test_backend_files_routes_require_api_key_when_enabled(async_client, endpoint, method, payload):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    if payload is None:
        response = await async_client.post(endpoint)
    else:
        response = await async_client.post(endpoint, json=payload)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
