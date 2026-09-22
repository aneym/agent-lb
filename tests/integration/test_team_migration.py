from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import _build_alembic_config, check_migration_policy, run_upgrade

pytestmark = pytest.mark.integration


def test_team_upgrade_downgrade_preserves_existing_key_and_settings(tmp_path):
    db_path = tmp_path / "team-migration.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    parent = "20260918_000000_add_usage_credits_window"
    head = "20260918_000000_add_team_members"
    run_upgrade(db_url, parent, bootstrap_legacy=False)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO api_keys (id, name, key_hash, key_prefix, is_active, created_at) "
                    "VALUES ('existing', 'Existing', 'hash', 'sk-clb-existing', 1, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text("UPDATE dashboard_settings SET api_key_auth_enabled=1 WHERE id=1")
            )
        run_upgrade(db_url, head, bootstrap_legacy=False)
        assert check_migration_policy(db_url) == ()
        with engine.begin() as connection:
            assert connection.execute(text("SELECT member_id, is_active FROM api_keys WHERE id='existing'")).one() == (
                None,
                1,
            )
            assert connection.execute(
                text("SELECT api_key_auth_enabled, team_mode_enabled, team_public_base_url FROM dashboard_settings")
            ).one() == (1, 0, None)
            connection.execute(text("INSERT INTO team_members (id, name) VALUES ('member', 'Member')"))
            connection.execute(text("UPDATE api_keys SET member_id='member' WHERE id='existing'"))
        command.downgrade(_build_alembic_config(db_url), parent)
        with engine.connect() as connection:
            assert not inspect(connection).has_table("team_members")
            assert connection.execute(text("SELECT name, is_active FROM api_keys WHERE id='existing'")).one() == (
                "Existing",
                1,
            )
            assert connection.execute(text("SELECT api_key_auth_enabled FROM dashboard_settings")).scalar_one() == 1
        run_upgrade(db_url, head, bootstrap_legacy=False)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT member_id FROM api_keys WHERE id='existing'")).scalar_one() is None
    finally:
        engine.dispose()
