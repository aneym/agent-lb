from __future__ import annotations

from typing import cast

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeysService,
    ApiKeyUpdateData,
)
from tests.floor.api_keys_service_support import (
    _FakeApiKeysRepository,
    _FakeUsageRepository,
    _make_usage_history,
    _TransactionalAssignmentFailureRepo,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_update_key_tracks_assignment_scope_after_clear() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    repo._accounts = {
        "acc-a": Account(
            id="acc-a",
            chatgpt_account_id=None,
            email="a@example.com",
            plan_type="plus",
            access_token_encrypted=b"access",
            refresh_token_encrypted=b"refresh",
            id_token_encrypted=b"id",
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        ),
    }

    created = await service.create_key(
        ApiKeyCreateData(
            name="assignment-scope",
            allowed_models=None,
            expires_at=None,
        )
    )

    scoped = await service.update_key(
        created.id,
        ApiKeyUpdateData(
            assigned_account_ids=["acc-a"],
            assigned_account_ids_set=True,
        ),
    )
    assert scoped.account_assignment_scope_enabled is True
    assert scoped.assigned_account_ids == ["acc-a"]

    cleared = await service.update_key(
        created.id,
        ApiKeyUpdateData(
            assigned_account_ids=[],
            assigned_account_ids_set=True,
        ),
    )
    assert cleared.account_assignment_scope_enabled is False
    assert cleared.assigned_account_ids == []


@pytest.mark.asyncio
async def test_create_key_persists_assigned_accounts() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    repo._accounts = {
        "acc-a": Account(
            id="acc-a",
            chatgpt_account_id=None,
            email="a@example.com",
            plan_type="plus",
            access_token_encrypted=b"access",
            refresh_token_encrypted=b"refresh",
            id_token_encrypted=b"id",
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        ),
    }

    created = await service.create_key(
        ApiKeyCreateData(
            name="assignment-scope",
            allowed_models=None,
            expires_at=None,
            assigned_account_ids=["acc-a"],
        )
    )

    assert created.account_assignment_scope_enabled is True
    assert created.assigned_account_ids == ["acc-a"]


@pytest.mark.asyncio
async def test_create_key_rolls_back_when_account_assignment_write_fails() -> None:
    repo = _TransactionalAssignmentFailureRepo()
    service = ApiKeysService(repo)
    repo._accounts = {
        "acc-a": Account(
            id="acc-a",
            chatgpt_account_id=None,
            email="a@example.com",
            plan_type="plus",
            access_token_encrypted=b"access",
            refresh_token_encrypted=b"refresh",
            id_token_encrypted=b"id",
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        ),
    }

    with pytest.raises(RuntimeError, match="assignment insert failed"):
        await service.create_key(
            ApiKeyCreateData(
                name="assignment-scope",
                allowed_models=None,
                expires_at=None,
                assigned_account_ids=["acc-a"],
            )
        )

    assert repo.commit_calls == 0
    assert repo.rollback_calls == 1
    assert repo.rows == {}


@pytest.mark.asyncio
async def test_create_key_rejects_unknown_assigned_accounts() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    with pytest.raises(ValueError, match="Unknown account ids: missing-account"):
        await service.create_key(
            ApiKeyCreateData(
                name="assignment-scope",
                allowed_models=None,
                expires_at=None,
                assigned_account_ids=["missing-account"],
            )
        )


@pytest.mark.asyncio
async def test_list_keys_uses_only_assigned_accounts_for_pooled_credits() -> None:
    repo = _FakeApiKeysRepository()
    repo._accounts = {
        "acc-a": Account(
            id="acc-a",
            chatgpt_account_id=None,
            email="a@example.com",
            plan_type="plus",
            access_token_encrypted=b"access-a",
            refresh_token_encrypted=b"refresh-a",
            id_token_encrypted=b"id-a",
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        ),
        "acc-b": Account(
            id="acc-b",
            chatgpt_account_id=None,
            email="b@example.com",
            plan_type="plus",
            access_token_encrypted=b"access-b",
            refresh_token_encrypted=b"refresh-b",
            id_token_encrypted=b"id-b",
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        ),
    }
    service = ApiKeysService(
        repo,
        _FakeUsageRepository(
            primary={
                "acc-a": _make_usage_history("acc-a", used_percent=25.0),
                "acc-b": _make_usage_history("acc-b", used_percent=75.0),
            },
            secondary={
                "acc-a": _make_usage_history("acc-a", used_percent=10.0),
                "acc-b": _make_usage_history("acc-b", used_percent=60.0),
            },
        ),
    )

    created = await service.create_key(
        ApiKeyCreateData(
            name="assigned-only",
            allowed_models=None,
            expires_at=None,
            assigned_account_ids=["acc-a"],
        )
    )

    account_calls_before_list = len(repo.list_accounts_by_ids_calls)
    listed = await service.list_keys()
    account_calls_after_list = repo.list_accounts_by_ids_calls[account_calls_before_list:]

    assert listed[0].id == created.id
    assert listed[0].pooled_credits is not None
    assert repo.list_all_accounts_calls == 0
    assert account_calls_after_list == [["acc-a"]]
    assert cast(_FakeUsageRepository, service._usage_repository).calls == [
        ("primary", ["acc-a"]),
        ("secondary", ["acc-a"]),
    ]


@pytest.mark.asyncio
async def test_list_keys_falls_back_to_all_accounts_when_key_is_unassigned() -> None:
    repo = _FakeApiKeysRepository()
    repo._accounts = {
        "acc-a": Account(
            id="acc-a",
            chatgpt_account_id=None,
            email="a@example.com",
            plan_type="plus",
            access_token_encrypted=b"access-a",
            refresh_token_encrypted=b"refresh-a",
            id_token_encrypted=b"id-a",
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        ),
        "acc-b": Account(
            id="acc-b",
            chatgpt_account_id=None,
            email="b@example.com",
            plan_type="plus",
            access_token_encrypted=b"access-b",
            refresh_token_encrypted=b"refresh-b",
            id_token_encrypted=b"id-b",
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        ),
    }
    usage_repo = _FakeUsageRepository(
        primary={
            "acc-a": _make_usage_history("acc-a", used_percent=25.0),
            "acc-b": _make_usage_history("acc-b", used_percent=75.0),
        },
        secondary={
            "acc-a": _make_usage_history("acc-a", used_percent=10.0),
            "acc-b": _make_usage_history("acc-b", used_percent=60.0),
        },
    )
    service = ApiKeysService(repo, usage_repo)

    await service.create_key(
        ApiKeyCreateData(
            name="unassigned",
            allowed_models=None,
            expires_at=None,
        )
    )

    account_calls_before_list = len(repo.list_accounts_by_ids_calls)
    listed = await service.list_keys()
    account_calls_after_list = repo.list_accounts_by_ids_calls[account_calls_before_list:]

    assert listed[0].pooled_credits is not None
    assert repo.list_all_accounts_calls == 1
    assert account_calls_after_list == []
    assert usage_repo.calls == [("primary", None), ("secondary", None)]
