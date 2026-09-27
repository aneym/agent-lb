"""Fence token exchanges and preserve OAuth access expiry.

Revision ID: 20260926_000000_refresh_intent_expiry
Revises: 20260926_200000_add_team_pool_share
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260926_000000_refresh_intent_expiry"
down_revision = "20260926_200000_add_team_pool_share"
branch_labels = None
depends_on = None

_OLD = ("active", "rate_limited", "quota_exceeded", "paused", "reauth_required", "deactivated")
_NEW = (*_OLD, "exchange_uncertain")


def _enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name="account_status", validate_strings=True, create_type=False)


_TRANSFER_OLD = ("pending", "settled")
_TRANSFER_NEW = (*_TRANSFER_OLD, "aborting", "aborted")


def _transfer_enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name="account_transfer_state", validate_strings=True, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE account_status ADD VALUE IF NOT EXISTS 'exchange_uncertain'")
    else:
        with op.batch_alter_table("accounts") as batch:
            batch.alter_column("status", existing_type=_enum(_OLD), type_=_enum(_NEW), existing_nullable=False)
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE account_transfer_state ADD VALUE IF NOT EXISTS 'aborting'")
            op.execute("ALTER TYPE account_transfer_state ADD VALUE IF NOT EXISTS 'aborted'")
    else:
        with op.batch_alter_table("account_transfers") as batch:
            batch.alter_column(
                "state",
                existing_type=_transfer_enum(_TRANSFER_OLD),
                type_=_transfer_enum(_TRANSFER_NEW),
                existing_nullable=False,
            )
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    inspector = sa.inspect(op.get_bind())
    if "access_expires_at" not in {column["name"] for column in inspector.get_columns("accounts")}:
        op.add_column("accounts", sa.Column("access_expires_at", sa.DateTime(), nullable=True))
    if not inspector.has_table("account_exchange_intents"):
        op.create_table(
            "account_exchange_intents",
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("refresh_token_sha256", sa.String(length=64), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("replay", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("reason", sa.String(length=32), nullable=True),
        )


def downgrade() -> None:
    op.execute("UPDATE account_transfers SET state = 'settled' WHERE state IN ('aborted', 'aborting')")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("account_transfers") as batch:
            batch.alter_column(
                "state",
                existing_type=_transfer_enum(_TRANSFER_NEW),
                type_=_transfer_enum(_TRANSFER_OLD),
                existing_nullable=False,
            )
    elif bind.dialect.name == "postgresql":
        transfer_default = bind.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'account_transfers' AND column_name = 'state'"
            )
        ).scalar_one()
        if transfer_default is not None:
            op.execute("ALTER TABLE account_transfers ALTER COLUMN state DROP DEFAULT")
        op.execute("ALTER TYPE account_transfer_state RENAME TO account_transfer_state_old")
        op.execute("CREATE TYPE account_transfer_state AS ENUM ('pending', 'settled')")
        op.execute(
            "ALTER TABLE account_transfers ALTER COLUMN state TYPE account_transfer_state "
            "USING state::text::account_transfer_state"
        )
        if transfer_default is not None:
            op.execute("ALTER TABLE account_transfers ALTER COLUMN state SET DEFAULT " + transfer_default)
        op.execute("DROP TYPE account_transfer_state_old")
    op.drop_table("account_exchange_intents")
    op.drop_column("accounts", "access_expires_at")
    op.execute("UPDATE accounts SET status = 'reauth_required' WHERE status = 'exchange_uncertain'")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("accounts") as batch:
            batch.alter_column("status", existing_type=_enum(_NEW), type_=_enum(_OLD), existing_nullable=False)
    elif bind.dialect.name == "postgresql":
        status_default = bind.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'accounts' AND column_name = 'status'"
            )
        ).scalar_one()
        if status_default is not None:
            op.execute("ALTER TABLE accounts ALTER COLUMN status DROP DEFAULT")
        op.execute("ALTER TYPE account_status RENAME TO account_status_old")
        op.execute("CREATE TYPE account_status AS ENUM (" + ", ".join("'" + v + "'" for v in _OLD) + ")")
        op.execute("ALTER TABLE accounts ALTER COLUMN status TYPE account_status USING status::text::account_status")
        if status_default is not None:
            op.execute("ALTER TABLE accounts ALTER COLUMN status SET DEFAULT " + status_default)
        op.execute("DROP TYPE account_status_old")
