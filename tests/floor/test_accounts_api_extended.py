from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.auth import fallback_account_id, generate_unique_account_id
from app.core.utils.time import utcnow
from app.db.models import Account
from app.db.request_models import RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.deletion import run_account_deletion_pass
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from tests.floor.accounts_api_helpers import _make_account as _make_account
from tests.floor.accounts_api_helpers import _make_auth_json as _make_auth_json

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_import_invalid_json_returns_400(async_client):
    files = {"auth_json": ("auth.json", "not-json", "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_auth_json"


@pytest.mark.asyncio
async def test_import_missing_tokens_returns_400(async_client):
    files = {"auth_json": ("auth.json", json.dumps({"foo": "bar"}), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_auth_json"


@pytest.mark.asyncio
async def test_import_falls_back_to_email_based_account_id(async_client):
    email = "fallback@example.com"
    auth_json = _make_auth_json(None, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountId"] == fallback_account_id(email)
    assert payload["email"] == email


@pytest.mark.asyncio
async def test_import_overwrites_for_same_account_identity_when_overwrite_enabled(async_client):
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is False

    email = "same-default@example.com"
    raw_account_id = "acc_same_default"

    files_one = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "plus")),
            "application/json",
        )
    }
    first = await async_client.post("/api/accounts/import", files=files_one)
    assert first.status_code == 200

    files_two = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "team")),
            "application/json",
        )
    }
    second = await async_client.post("/api/accounts/import", files=files_two)
    assert second.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    assert first.json()["accountId"] == expected_account_id
    assert second.json()["accountId"] == expected_account_id
    assert second.json()["planType"] == "team"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 1
    assert accounts[0]["accountId"] == expected_account_id
    assert accounts[0]["planType"] == "team"


@pytest.mark.asyncio
async def test_import_without_overwrite_keeps_same_account_identity_separate(async_client):
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is True

    email = "same-separate@example.com"
    raw_account_id = "acc_same_separate"

    files_one = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "plus")),
            "application/json",
        )
    }
    first = await async_client.post("/api/accounts/import", files=files_one)
    assert first.status_code == 200

    files_two = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "team")),
            "application/json",
        )
    }
    second = await async_client.post("/api/accounts/import", files=files_two)
    assert second.status_code == 200

    base_account_id = generate_unique_account_id(raw_account_id, email)
    first_id = first.json()["accountId"]
    second_id = second.json()["accountId"]
    assert first_id == base_account_id
    assert second_id != first_id
    assert second_id.startswith(f"{base_account_id}__copy")

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 2
    ids = {entry["accountId"] for entry in accounts}
    assert ids == {first_id, second_id}


@pytest.mark.asyncio
async def test_import_updates_same_workspace_slot_even_when_duplicate_imports_allowed(async_client):
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200

    email = "workspace-same@example.com"
    raw_account_id = "acc_workspace_same"
    workspace_id = "ws_business_one"

    first = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(
                    _make_auth_json(
                        raw_account_id,
                        email,
                        "team",
                        workspace_id=workspace_id,
                        workspace_label="Business One",
                        seat_type="standard_chatgpt",
                    )
                ),
                "application/json",
            )
        },
    )
    assert first.status_code == 200

    second = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(
                    _make_auth_json(
                        raw_account_id,
                        email,
                        "business",
                        workspace_id=workspace_id,
                        workspace_label="Business One",
                        seat_type="codex",
                    )
                ),
                "application/json",
            )
        },
    )
    assert second.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email, workspace_id)
    assert first.json()["accountId"] == expected_account_id
    assert second.json()["accountId"] == expected_account_id
    assert second.json()["workspaceId"] == workspace_id
    assert second.json()["workspaceLabel"] == "Business One"
    assert second.json()["seatType"] == "codex"
    assert second.json()["planType"] == "business"

    accounts_response = await async_client.get("/api/accounts")
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 1
    assert accounts[0]["workspaceId"] == workspace_id
    assert accounts[0]["seatType"] == "codex"


@pytest.mark.asyncio
async def test_import_keeps_same_email_different_workspaces_separate(async_client):
    email = "workspace-two@example.com"
    raw_account_id = "acc_workspace_two"

    first = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email, "business", workspace_id="ws_one")),
                "application/json",
            )
        },
    )
    second = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email, "business", workspace_id="ws_two")),
                "application/json",
            )
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["accountId"] == generate_unique_account_id(raw_account_id, email, "ws_one")
    assert second.json()["accountId"] == generate_unique_account_id(raw_account_id, email, "ws_two")

    accounts_response = await async_client.get("/api/accounts")
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 2
    assert {entry["workspaceId"] for entry in accounts} == {"ws_one", "ws_two"}


@pytest.mark.asyncio
async def test_import_updates_email_workspace_slot_when_account_id_appears_later(async_client):
    email = "workspace-late-account@example.com"
    workspace_id = "ws_late_account"

    first = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(None, email, "business", workspace_id=workspace_id)),
                "application/json",
            )
        },
    )
    second = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json("acc_late_account", email, "business", workspace_id=workspace_id)),
                "application/json",
            )
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["accountId"] == first.json()["accountId"]

    accounts_response = await async_client.get("/api/accounts")
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 1
    assert accounts[0]["workspaceId"] == workspace_id

    async with SessionLocal() as session:
        stored = (
            await session.execute(
                select(Account).where(Account.email == email).where(Account.workspace_id == workspace_id)
            )
        ).scalar_one()
        assert stored.chatgpt_account_id == "acc_late_account"


@pytest.mark.asyncio
async def test_import_overwrite_mode_updates_single_unknown_workspace_email_slot(async_client):
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is False

    email = "overwrite-single@example.com"

    first = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(None, email, "plus")),
                "application/json",
            )
        },
    )
    second = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json("acc_overwrite_single", email, "pro")),
                "application/json",
            )
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["accountId"] == first.json()["accountId"]
    assert second.json()["planType"] == "pro"

    accounts_response = await async_client.get("/api/accounts")
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 1
    assert accounts[0]["planType"] == "pro"

    async with SessionLocal() as session:
        stored = (await session.execute(select(Account).where(Account.email == email))).scalar_one()
        assert stored.chatgpt_account_id == "acc_overwrite_single"


@pytest.mark.asyncio
async def test_import_returns_409_when_overwrite_mode_cannot_resolve_duplicate_email(async_client):
    enable_separate = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert enable_separate.status_code == 200
    assert enable_separate.json()["importWithoutOverwrite"] is True

    email = "conflict@example.com"
    raw_account_id = "acc_conflict_base"

    first = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email, "plus")),
                "application/json",
            )
        },
    )
    assert first.status_code == 200

    second = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json(raw_account_id, email, "team")),
                "application/json",
            )
        },
    )
    assert second.status_code == 200
    assert second.json()["accountId"] != first.json()["accountId"]

    enable_overwrite = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
        },
    )
    assert enable_overwrite.status_code == 200
    assert enable_overwrite.json()["importWithoutOverwrite"] is False

    conflict = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json("acc_conflict_new", email, "pro")),
                "application/json",
            )
        },
    )
    assert conflict.status_code == 409
    payload = conflict.json()
    assert payload["error"]["code"] == "duplicate_identity_conflict"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 2
    assert all(entry["planType"] != "pro" for entry in accounts)


@pytest.mark.asyncio
async def test_delete_account_removes_from_list(async_client):
    email = "delete@example.com"
    raw_account_id = "acc_delete"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    actual_account_id = generate_unique_account_id(raw_account_id, email)
    delete = await async_client.delete(f"/api/accounts/{actual_account_id}")
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert all(account["accountId"] != actual_account_id for account in data)


@pytest.mark.asyncio
async def test_delete_account_soft_deletes_request_logs(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_delete_logs", "delete-logs@example.com"))
        await logs_repo.add_log(
            account_id="acc_delete_logs",
            request_id="req_delete_logs_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=5,
            latency_ms=25,
            status="success",
            error_code=None,
            requested_at=utcnow(),
        )

    delete = await async_client.delete("/api/accounts/acc_delete_logs")
    assert delete.status_code == 200
    accounts = await async_client.get("/api/accounts")
    assert all(account["accountId"] != "acc_delete_logs" for account in accounts.json()["accounts"])
    assert (await run_account_deletion_pass())["acc_delete_logs"] == "finalized"

    async with SessionLocal() as session:
        row = (
            await session.execute(select(RequestLog).where(RequestLog.request_id == "req_delete_logs_1"))
        ).scalar_one()
        assert row.account_id is None
        assert row.deleted_at is not None

    request_logs = await async_client.get("/api/request-logs?limit=10")
    assert request_logs.status_code == 200
    assert request_logs.json()["total"] == 0

    options = await async_client.get("/api/request-logs/options")
    assert options.status_code == 200
    assert options.json()["accountIds"] == []


@pytest.mark.asyncio
async def test_delete_account_with_delete_history_hard_deletes_request_logs(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_hard_delete", "hard-delete@example.com"))
        await logs_repo.add_log(
            account_id="acc_hard_delete",
            request_id="req_hard_delete_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=5,
            latency_ms=25,
            status="success",
            error_code=None,
            requested_at=utcnow(),
        )

    delete = await async_client.delete("/api/accounts/acc_hard_delete?delete_history=true")
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"
    accounts = await async_client.get("/api/accounts")
    assert all(account["accountId"] != "acc_hard_delete" for account in accounts.json()["accounts"])
    assert (await run_account_deletion_pass())["acc_hard_delete"] == "finalized"

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_hard_delete_1"))
        assert result.scalar_one_or_none() is None

    request_logs = await async_client.get("/api/request-logs?limit=10")
    assert request_logs.status_code == 200
    assert request_logs.json()["total"] == 0
