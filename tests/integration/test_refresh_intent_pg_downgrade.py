"""PostgreSQL enum rollback preserves data and column defaults."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.migrate import _build_alembic_config, run_upgrade
from app.db.migration_url import to_sync_database_url

pytestmark = pytest.mark.integration
_HEAD = "20260926_000000_refresh_intent_expiry"
_PARENT = "20260926_200000_add_team_pool_share"


def _column_default(connection, table: str, column: str) -> str | None:
    return connection.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).scalar_one()


def _enum_labels(connection, name: str) -> list[str]:
    return (
        connection.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = current_schema() AND t.typname = :name ORDER BY e.enumsortorder"
            ),
            {"name": name},
        )
        .scalars()
        .all()
    )


def test_refresh_intent_pg_downgrade_preserves_defaults_and_enum_values() -> None:
    url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL not set")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not (parsed.database or "").startswith("agent_lb_si_scratch_"):
        pytest.fail("PostgreSQL refresh-intent migration tests require a disposable SI scratch database")

    run_upgrade(url, _HEAD, bootstrap_legacy=False)
    suffix = uuid4().hex
    account_id = f"pgdown-account-{suffix}"
    transfer_ids = [f"pgdown-aborting-{suffix}", f"pgdown-aborted-{suffix}"]
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.begin() as connection:
            # Exercise both enum swaps even if this scratch schema has no account status default.
            if _column_default(connection, "accounts", "status") is None:
                connection.execute(
                    text("ALTER TABLE accounts ALTER COLUMN status SET DEFAULT 'active'::account_status")
                )
            transfer_default = _column_default(connection, "account_transfers", "state")
            account_default = _column_default(connection, "accounts", "status")
            assert transfer_default is not None
            assert account_default is not None
            connection.execute(
                text(
                    "INSERT INTO accounts (id, provider, email, plan_type, access_token_encrypted, "
                    "refresh_token_encrypted, last_refresh, status, owner_instance) "
                    "VALUES (:id, 'anthropic', :email, 'pro', :access, :refresh, "
                    ":last_refresh, 'exchange_uncertain', 'peer')"
                ),
                {
                    "id": account_id,
                    "email": f"pgdown-{suffix}@example.invalid",
                    "access": b"access",
                    "refresh": b"refresh",
                    "last_refresh": "2026-09-26 00:00:00",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO account_transfers "
                    "(id, account_id, nonce, direction, counterparty_instance_id, state) "
                    "VALUES (:id, :account_id, :nonce, :direction, 'peer', :state)"
                ),
                [
                    {
                        "id": transfer_ids[0],
                        "account_id": account_id,
                        "nonce": f"pgdown-nonce-aborting-{suffix}",
                        "direction": "checkout",
                        "state": "aborting",
                    },
                    {
                        "id": transfer_ids[1],
                        "account_id": account_id,
                        "nonce": f"pgdown-nonce-aborted-{suffix}",
                        "direction": "checkin",
                        "state": "aborted",
                    },
                ],
            )

        command.downgrade(_build_alembic_config(url), _PARENT)
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT status FROM accounts WHERE id = :id"), {"id": account_id}).scalar_one()
                == "reauth_required"
            )
            assert connection.execute(
                text("SELECT state FROM account_transfers WHERE id IN (:first, :second) ORDER BY id"),
                {"first": transfer_ids[0], "second": transfer_ids[1]},
            ).scalars().all() == ["settled", "settled"]
            assert _column_default(connection, "account_transfers", "state") == transfer_default
            assert _column_default(connection, "accounts", "status") == account_default
            assert _enum_labels(connection, "account_transfer_state") == ["pending", "settled"]
            assert _enum_labels(connection, "account_status") == [
                "active",
                "rate_limited",
                "quota_exceeded",
                "paused",
                "reauth_required",
                "deactivated",
            ]
        run_upgrade(url, _HEAD, bootstrap_legacy=False)
    finally:
        engine.dispose()
