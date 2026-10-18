from __future__ import annotations

import pytest
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.auth import DEFAULT_PLAN
from app.db.migrate import (
    check_schema_drift,
    run_startup_migrations,
    run_upgrade,
)
from app.db.models import Account, Base
from app.db.session import SessionLocal
from tests.integration.migrations_support import (
    _DATABASE_URL,
    _HEAD_REVISION,
    _is_postgresql_database_url,
    _make_account,
    check_migration_policy,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_run_startup_migrations_preserves_unknown_plan_types(db_setup):
    engine = create_async_engine(_DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await to_thread.run_sync(lambda: run_upgrade(_DATABASE_URL, "20260213_000000_base_schema"))
        async with engine.begin() as connection:
            for account in (
                _make_account("acc_one", "one@example.com", "education"),
                _make_account("acc_two", "two@example.com", "PRO"),
                _make_account("acc_three", "three@example.com", ""),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO accounts (id, email, plan_type, access_token_encrypted, "
                        "refresh_token_encrypted, id_token_encrypted, last_refresh, status) "
                        "VALUES (:id, :email, :plan_type, :access, :refresh, :id_token, :last_refresh, :status)"
                    ),
                    dict(
                        id=account.id,
                        email=account.email,
                        plan_type=account.plan_type,
                        access=account.access_token_encrypted,
                        refresh=account.refresh_token_encrypted,
                        id_token=account.id_token_encrypted,
                        last_refresh=account.last_refresh,
                        status="active",
                    ),
                )
    finally:
        await engine.dispose()

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        acc_one = await session.get(Account, "acc_one")
        acc_two = await session.get(Account, "acc_two")
        acc_three = await session.get(Account, "acc_three")
        assert acc_one is not None
        assert acc_two is not None
        assert acc_three is not None
        assert acc_one.plan_type == "education"
        assert acc_two.plan_type == "pro"
        assert acc_three.plan_type == DEFAULT_PLAN

    rerun = await run_startup_migrations(_DATABASE_URL)
    assert rerun.current_revision == _HEAD_REVISION


@pytest.mark.asyncio
@pytest.mark.skipif(
    (not _is_postgresql_database_url(_DATABASE_URL)) or check_migration_policy is None,
    reason="PostgreSQL-only migration contract test",
)
async def test_postgresql_migration_contract_policy_and_drift_match(db_setup):
    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    assert check_migration_policy is not None
    assert check_migration_policy(_DATABASE_URL) == ()
    assert check_schema_drift(_DATABASE_URL) == ()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _is_postgresql_database_url(_DATABASE_URL),
    reason="PostgreSQL-only empty database migration test",
)
async def test_postgresql_upgrade_head_from_empty_database(db_setup):
    async with SessionLocal() as session:
        await session.execute(text("DROP SCHEMA public CASCADE"))
        await session.execute(text("CREATE SCHEMA public"))
        await session.commit()

    result = await run_startup_migrations(_DATABASE_URL)
    assert result.current_revision == _HEAD_REVISION

    async with SessionLocal() as session:
        revision_rows = await session.execute(text("SELECT version_num FROM alembic_version"))
        revisions = sorted(str(row[0]) for row in revision_rows.fetchall())
        assert revisions == [_HEAD_REVISION]
