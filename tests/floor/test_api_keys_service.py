from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import OperationalError

from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeysService,
    ApiKeyUpdateData,
    _is_sqlite_database_locked,
)
from tests.floor.api_keys_service_support import (
    _FakeApiKeysRepository,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "message",
    [
        "database is locked",
        "database table is locked",
        "database schema is locked",
    ],
)
def test_is_sqlite_database_locked_matches_transient_lock_messages(message: str) -> None:
    assert _is_sqlite_database_locked(OperationalError("sqlite busy", {}, Exception(message))) is True


@pytest.mark.asyncio
async def test_create_key_stores_hash_and_prefix() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="dev-key",
            allowed_models=["o3-pro"],
            expires_at=None,
        )
    )

    assert created.key.startswith("sk-clb-")
    assert created.key_prefix == created.key[:15]
    assert created.allowed_models == ["o3-pro"]
    assert created.traffic_class == "foreground"

    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.key_hash != created.key
    assert stored.key_prefix == created.key[:15]
    assert stored.traffic_class == "foreground"


@pytest.mark.asyncio
async def test_create_key_persists_opportunistic_traffic_class() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="fixer-nucat-worker",
            allowed_models=None,
            traffic_class="opportunistic",
            expires_at=None,
        )
    )

    assert created.traffic_class == "opportunistic"
    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.traffic_class == "opportunistic"


@pytest.mark.asyncio
async def test_update_key_changes_traffic_class() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(ApiKeyCreateData(name="worker", allowed_models=None))

    updated = await service.update_key(
        created.id,
        ApiKeyUpdateData(traffic_class="opportunistic", traffic_class_set=True),
    )

    assert updated.traffic_class == "opportunistic"


@pytest.mark.asyncio
async def test_api_key_read_skips_null_allowed_models() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="nullable-models",
            allowed_models=["gpt-5.2"],
            expires_at=None,
        )
    )
    row = await repo.get_by_id(created.id)
    assert row is not None
    row.allowed_models = '[null, " gpt-5.2 ", 42, "", "gpt-5.5"]'

    reloaded = await service.get_key_by_id(created.id)

    assert reloaded.allowed_models == ["gpt-5.2", "gpt-5.5"]


@pytest.mark.asyncio
async def test_create_key_normalizes_timezone_aware_expiry_to_utc_naive() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="expiring-key",
            allowed_models=None,
            expires_at=datetime(2026, 3, 20, 23, 59, 59, tzinfo=timezone(timedelta(hours=9))),
        )
    )

    assert created.expires_at == datetime(2026, 3, 20, 14, 59, 59)

    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.expires_at == datetime(2026, 3, 20, 14, 59, 59)


@pytest.mark.asyncio
async def test_create_key_rejects_enforced_model_outside_allowed_models() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    with pytest.raises(ValueError, match="enforced_model"):
        await service.create_key(
            ApiKeyCreateData(
                name="invalid-policy",
                allowed_models=["model-alpha"],
                enforced_model="model-beta",
                expires_at=None,
            )
        )


@pytest.mark.asyncio
async def test_create_key_normalizes_enforced_reasoning_effort() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="reasoning-policy",
            allowed_models=None,
            enforced_reasoning_effort="HIGH",
            expires_at=None,
        )
    )

    assert created.enforced_reasoning_effort == "high"


@pytest.mark.asyncio
async def test_create_key_persists_apply_to_codex_model_flag() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="codex-visibility-policy",
            allowed_models=["gpt-5.2"],
            apply_to_codex_model=True,
            expires_at=None,
        )
    )

    assert created.apply_to_codex_model is True

    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.apply_to_codex_model is True


@pytest.mark.asyncio
async def test_create_key_normalizes_fast_service_tier_alias() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="service-tier-policy",
            allowed_models=None,
            enforced_service_tier="FAST",
            expires_at=None,
        )
    )

    assert created.enforced_service_tier == "priority"


@pytest.mark.asyncio
async def test_update_key_normalizes_service_tier_alias() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="service-tier-update",
            allowed_models=None,
            expires_at=None,
        )
    )

    updated = await service.update_key(
        created.id,
        ApiKeyUpdateData(
            enforced_service_tier="fast",
            enforced_service_tier_set=True,
        ),
    )

    assert updated.enforced_service_tier == "priority"


@pytest.mark.asyncio
async def test_update_key_persists_apply_to_codex_model_flag() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="codex-visibility-update",
            allowed_models=["gpt-5.2"],
            expires_at=None,
        )
    )

    updated = await service.update_key(
        created.id,
        ApiKeyUpdateData(
            apply_to_codex_model=True,
            apply_to_codex_model_set=True,
        ),
    )

    assert updated.apply_to_codex_model is True

    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.apply_to_codex_model is True


@pytest.mark.asyncio
async def test_update_key_ignores_null_apply_to_codex_model_patch_value() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="codex-visibility-null-update",
            allowed_models=["gpt-5.2"],
            apply_to_codex_model=True,
            expires_at=None,
        )
    )

    updated = await service.update_key(
        created.id,
        ApiKeyUpdateData(
            apply_to_codex_model=None,
            apply_to_codex_model_set=True,
        ),
    )

    assert updated.apply_to_codex_model is True

    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.apply_to_codex_model is True


@pytest.mark.asyncio
async def test_update_key_normalizes_timezone_aware_expiry_to_utc_naive() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(ApiKeyCreateData(name="update-expiry", allowed_models=None, expires_at=None))

    updated = await service.update_key(
        created.id,
        ApiKeyUpdateData(
            expires_at=datetime(2026, 4, 1, 5, 30, 0, tzinfo=timezone(timedelta(hours=-7))),
            expires_at_set=True,
        ),
    )

    assert updated.expires_at == datetime(2026, 4, 1, 12, 30, 0)

    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.expires_at == datetime(2026, 4, 1, 12, 30, 0)


@pytest.mark.asyncio
async def test_regenerate_key_rotates_hash_and_prefix() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(ApiKeyCreateData(name="regen-key", allowed_models=None, expires_at=None))

    row_before = await repo.get_by_id(created.id)
    assert row_before is not None
    old_hash = row_before.key_hash
    old_prefix = row_before.key_prefix

    regenerated = await service.regenerate_key(created.id)
    row_after = await repo.get_by_id(created.id)
    assert row_after is not None

    assert regenerated.key.startswith("sk-clb-")
    assert row_after.key_hash != old_hash
    assert row_after.key_prefix != old_prefix
