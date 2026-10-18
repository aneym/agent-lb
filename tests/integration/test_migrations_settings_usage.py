from __future__ import annotations

import pytest
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.migrate import (
    run_upgrade,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_dashboard_settings_default_flip_migration_does_not_infer_intent_from_updated_at(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-settings-defaults.sqlite'}"
    base_revision = "20260408_010000_merge_import_without_overwrite_and_assignment_heads"

    await to_thread.run_sync(lambda: run_upgrade(db_url, base_revision))

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE dashboard_settings
                    SET sticky_threads_enabled = 0,
                        prefer_earlier_reset_accounts = 0,
                        password_hash = 'bcrypt$demo',
                        updated_at = '2026-02-01 00:00:00'
                    WHERE id = 1
                    """
                )
            )
            await session.commit()

        await to_thread.run_sync(
            lambda: run_upgrade(
                db_url, "20260409_000000_switch_sticky_threads_and_prefer_earlier_reset_defaults_to_true"
            )
        )

        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT sticky_threads_enabled, prefer_earlier_reset_accounts
                        FROM dashboard_settings
                        WHERE id = 1
                        """
                    )
                )
            ).one()
            assert row[0] in (False, 0)
            assert row[1] in (False, 0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_settings_default_flip_migration_updates_fresh_seeded_row(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-settings-defaults-fresh.sqlite'}"

    await to_thread.run_sync(
        lambda: run_upgrade(db_url, "20260409_000000_switch_sticky_threads_and_prefer_earlier_reset_defaults_to_true")
    )

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT sticky_threads_enabled, prefer_earlier_reset_accounts
                        FROM dashboard_settings
                        WHERE id = 1
                        """
                    )
                )
            ).one()
            assert row[0] in (True, 1)
            assert row[1] in (True, 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_settings_default_flip_migration_updates_pristine_fresh_db_upgraded_in_steps(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-settings-defaults-staged-fresh.sqlite'}"

    await to_thread.run_sync(
        lambda: run_upgrade(db_url, "20260408_010000_merge_import_without_overwrite_and_assignment_heads")
    )

    await to_thread.run_sync(lambda: run_upgrade(db_url, "head"))

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT sticky_threads_enabled, prefer_earlier_reset_accounts
                        FROM dashboard_settings
                        WHERE id = 1
                        """
                    )
                )
            ).one()
            assert row[0] in (True, 1)
            assert row[1] in (True, 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_free_account_monthly_migration_renames_only_free_usage_windows(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'free-monthly-migration.sqlite'}"

    await to_thread.run_sync(lambda: run_upgrade(db_url, "20260604_000000_add_reauth_required_account_status"))

    engine = create_async_engine(db_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO accounts (
                        id, email, plan_type,
                        access_token_encrypted, refresh_token_encrypted, id_token_encrypted,
                        last_refresh, status
                    )
                    VALUES
                      ('acc_free_monthly_migration', 'free-monthly@example.com', 'free',
                       x'01', x'02', x'03', '2026-01-01 00:00:00', 'active'),
                      ('acc_paid_monthly_migration', 'paid-monthly@example.com', 'plus',
                       x'04', x'05', x'06', '2026-01-01 00:00:00', 'active')
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO usage_history (account_id, recorded_at, window, used_percent)
                    VALUES
                      ('acc_free_monthly_migration', CURRENT_TIMESTAMP, 'primary', 10.0),
                      ('acc_free_monthly_migration', CURRENT_TIMESTAMP, 'secondary', 20.0),
                      ('acc_free_monthly_migration', CURRENT_TIMESTAMP, NULL, 25.0),
                      ('acc_paid_monthly_migration', CURRENT_TIMESTAMP, 'primary', 30.0),
                      ('acc_paid_monthly_migration', CURRENT_TIMESTAMP, 'secondary', 40.0),
                      ('acc_paid_monthly_migration', CURRENT_TIMESTAMP, NULL, 45.0)
                    """
                )
            )
            await session.commit()

        await to_thread.run_sync(lambda: run_upgrade(db_url, "head"))

        async with session_factory() as session:
            free_windows = [
                row[0]
                for row in (
                    await session.execute(
                        text("SELECT window FROM usage_history WHERE account_id = :account_id ORDER BY id"),
                        {"account_id": "acc_free_monthly_migration"},
                    )
                ).all()
            ]
            paid_windows = [
                row[0]
                for row in (
                    await session.execute(
                        text("SELECT window FROM usage_history WHERE account_id = :account_id ORDER BY id"),
                        {"account_id": "acc_paid_monthly_migration"},
                    )
                ).all()
            ]
    finally:
        await engine.dispose()

    assert free_windows == ["old-primary", "old-secondary", "old-primary"]
    assert paid_windows == ["primary", "secondary", None]
