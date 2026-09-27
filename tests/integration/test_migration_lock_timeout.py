from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.db.migrate import current_revision, run_upgrade
from app.db.migration_url import to_sync_database_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "postgresql" not in os.environ.get("AGENT_LB_TEST_DATABASE_URL", ""), reason="requires Postgres"
    ),
]


@pytest.mark.timeout(90)
def test_refresh_migration_fails_fast_on_account_lock_then_retries():
    source = make_url(os.environ["AGENT_LB_TEST_DATABASE_URL"])
    database_name = f"agentlb_r4r_lock_{uuid4().hex[:12]}"
    admin = create_engine(
        to_sync_database_url(source.set(database="postgres").render_as_string(hide_password=False)),
        isolation_level="AUTOCOMMIT",
    )
    url = source.set(database=database_name).render_as_string(hide_password=False)
    parent = "20260926_200000_add_team_pool_share"
    head = "20260926_000000_refresh_intent_expiry"
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    engine = create_engine(to_sync_database_url(url))
    try:
        run_upgrade(url, parent, bootstrap_legacy=False)
        with engine.connect() as blocker:
            transaction = blocker.begin()
            blocker.execute(text("LOCK TABLE accounts IN ACCESS SHARE MODE"))
            started = time.monotonic()
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    job = pool.submit(run_upgrade, url, head, bootstrap_legacy=False)
                    with pytest.raises(Exception, match="lock timeout"):
                        job.result(timeout=15)
            finally:
                transaction.rollback()
            assert time.monotonic() - started < 15
        assert current_revision(url) == parent
        run_upgrade(url, head, bootstrap_legacy=False)
        assert current_revision(url) == head
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "exchange_uncertain" in {
                value for (value,) in connection.execute(text("SELECT unnest(enum_range(NULL::account_status))::text"))
            }
            assert "access_expires_at" in {column["name"] for column in inspector.get_columns("accounts")}
            assert inspector.has_table("account_exchange_intents")
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin.dispose()
