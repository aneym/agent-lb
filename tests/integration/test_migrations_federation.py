from __future__ import annotations

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import BigInteger, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import (
    _build_alembic_config,
    run_startup_migrations,
    run_upgrade,
)
from app.db.models import Base
from app.db.session import SessionLocal
from tests.integration.migrations_support import (
    _DATABASE_URL,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_federation_usage_daily_migration_up_and_down(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'federation-usage.sqlite'}"
    parent_revision = "20260715_160000_add_request_logs_session_time_index"

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent_revision))
    await to_thread.run_sync(lambda: run_upgrade(db_url, "head"))

    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as connection:
            table = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='federation_usage_daily'")
            )
            assert table.scalar_one() == "federation_usage_daily"
    finally:
        await engine.dispose()

    config = _build_alembic_config(db_url)
    await to_thread.run_sync(lambda: command.downgrade(config, parent_revision))

    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as connection:
            table = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='federation_usage_daily'")
            )
            assert table.scalar_one_or_none() is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_federation_bigint_migration_preserves_rollups(db_setup):
    parent = "20261014_050000_add_additional_latest_indexes"
    config = _build_alembic_config(_DATABASE_URL)
    engine = create_async_engine(_DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()
    await run_startup_migrations(_DATABASE_URL)
    await to_thread.run_sync(lambda: command.downgrade(config, parent))
    counters = ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "session_count")
    async with SessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO federation_usage_daily "
                "(instance_id, account_id, provider, day, requests, input_tokens, output_tokens, "
                "cache_read_tokens, cost, session_count, reported_at) "
                "VALUES ('migration-peer', 'migration-account', 'anthropic', '2026-10-14', 1, 2, 3, 4, 1.25, 5, "
                "'2026-10-14 00:00:00')"
            )
        )
        await session.commit()
    await to_thread.run_sync(lambda: run_upgrade(_DATABASE_URL, "head"))
    async with SessionLocal() as session:
        values = (
            await session.execute(
                text(
                    "SELECT requests, input_tokens, output_tokens, cache_read_tokens, session_count "
                    "FROM federation_usage_daily WHERE instance_id = 'migration-peer'"
                )
            )
        ).one()
        assert tuple(values) == (1, 2, 3, 4, 5)
        connection = await session.connection()
        columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("federation_usage_daily"))
        assert all(isinstance(column["type"], BigInteger) for column in columns if column["name"] in counters)
        await session.execute(
            text("UPDATE federation_usage_daily SET " + ", ".join(f"{column} = :value" for column in counters)),
            {"value": 3260172888},
        )
        await session.commit()
    async with SessionLocal() as session:
        values = (
            await session.execute(
                text(
                    "SELECT requests, input_tokens, output_tokens, cache_read_tokens, session_count "
                    "FROM federation_usage_daily WHERE instance_id = 'migration-peer'"
                )
            )
        ).one()
        assert tuple(values) == (3260172888,) * len(counters)
