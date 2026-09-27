"""Request-log identity columns survive an existing row and a downgrade cycle."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.db.migrate import _build_alembic_config, run_upgrade
from app.db.migration_url import to_sync_database_url

pytestmark = pytest.mark.integration
_PARENT = "20260926_200000_add_team_pool_share"
_FIELDS = {"caller_user", "caller_user_source", "caller_machine", "caller_machine_source"}


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_request_identity_migration_preserves_old_rows_and_round_trips(tmp_path, backend):
    if backend == "sqlite":
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'request-identity.sqlite'}"
    else:
        db_url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
        if not db_url:
            pytest.skip("POSTGRES_TEST_DATABASE_URL not set")
        parsed = make_url(db_url)
        if parsed.get_backend_name() != "postgresql" or not (parsed.database or "").startswith(
            "agent_lb_identity_scratch_"
        ):
            pytest.fail("PostgreSQL identity migration tests require a disposable identity scratch database")

    # Start at head and step down, so the test behaves the same on a fresh or a reused scratch database.
    run_upgrade(db_url, "head", bootstrap_legacy=False)
    command.downgrade(_build_alembic_config(db_url), _PARENT)
    old_row = f"before-identity-{uuid4().hex}"
    engine = create_engine(to_sync_database_url(db_url))
    try:
        with engine.begin() as connection:
            assert not (_FIELDS & {column["name"] for column in inspect(connection).get_columns("request_logs")})
            connection.execute(
                text("INSERT INTO request_logs (request_id, model, status) VALUES (:rid, 'model-a', 'ok')"),
                {"rid": old_row},
            )

        for _ in range(2):
            run_upgrade(db_url, "head", bootstrap_legacy=False)
            with engine.connect() as connection:
                assert _FIELDS <= {column["name"] for column in inspect(connection).get_columns("request_logs")}
                assert connection.execute(
                    text(
                        "SELECT caller_user, caller_user_source, caller_machine, caller_machine_source "
                        "FROM request_logs WHERE request_id=:rid"
                    ),
                    {"rid": old_row},
                ).one() == (None, None, None, None)

            command.downgrade(_build_alembic_config(db_url), _PARENT)
            with engine.connect() as connection:
                assert not (_FIELDS & {column["name"] for column in inspect(connection).get_columns("request_logs")})
                assert (
                    connection.execute(
                        text("SELECT request_id FROM request_logs WHERE request_id=:rid"), {"rid": old_row}
                    ).scalar_one()
                    == old_row
                )
    finally:
        engine.dispose()
