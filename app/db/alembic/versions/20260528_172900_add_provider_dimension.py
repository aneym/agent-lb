"""add provider dimension

Revision ID: 20260528_172900_add_provider_dimension
Revises: 20260525_000000_add_usage_raw_window_latest_index
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260528_172900_add_provider_dimension"
down_revision = "20260525_000000_add_usage_raw_window_latest_index"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> dict[str, dict[str, object]]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return {}
    return {str(column["name"]): dict(column) for column in inspector.get_columns(table_name)}


def _add_column_if_missing(
    connection: Connection,
    table_name: str,
    column_name: str,
    column: sa.Column,
) -> None:
    if column_name in _columns(connection, table_name):
        return
    op.add_column(table_name, column)


def _drop_column_if_present(connection: Connection, table_name: str, column_name: str) -> None:
    if column_name not in _columns(connection, table_name):
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_column(column_name)


def upgrade() -> None:
    bind = op.get_bind()

    if _columns(bind, "accounts"):
        _add_column_if_missing(
            bind,
            "accounts",
            "provider",
            sa.Column("provider", sa.String(), nullable=False, server_default=sa.text("'openai'")),
        )
        account_columns = _columns(bind, "accounts")
        if account_columns.get("id_token_encrypted", {}).get("nullable") is False:
            with op.batch_alter_table("accounts") as batch_op:
                batch_op.alter_column("id_token_encrypted", existing_type=sa.LargeBinary(), nullable=True)

    if _columns(bind, "usage_history"):
        _add_column_if_missing(
            bind,
            "usage_history",
            "provider",
            sa.Column("provider", sa.String(), nullable=False, server_default=sa.text("'openai'")),
        )

    if _columns(bind, "request_logs"):
        _add_column_if_missing(
            bind,
            "request_logs",
            "provider",
            sa.Column("provider", sa.String(), nullable=False, server_default=sa.text("'openai'")),
        )
        _add_column_if_missing(
            bind,
            "request_logs",
            "cache_creation_tokens",
            sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
        )
        _add_column_if_missing(
            bind,
            "request_logs",
            "cache_read_tokens",
            sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _columns(bind, "request_logs"):
        _drop_column_if_present(bind, "request_logs", "cache_read_tokens")
        _drop_column_if_present(bind, "request_logs", "cache_creation_tokens")
        _drop_column_if_present(bind, "request_logs", "provider")

    if _columns(bind, "usage_history"):
        _drop_column_if_present(bind, "usage_history", "provider")

    if _columns(bind, "accounts"):
        bind.execute(sa.text("UPDATE accounts SET id_token_encrypted = x'' WHERE id_token_encrypted IS NULL"))
        account_columns = _columns(bind, "accounts")
        if account_columns.get("id_token_encrypted", {}).get("nullable") is True:
            with op.batch_alter_table("accounts") as batch_op:
                batch_op.alter_column("id_token_encrypted", existing_type=sa.LargeBinary(), nullable=False)
        _drop_column_if_present(bind, "accounts", "provider")
