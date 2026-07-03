"""add account_transfers table for instance-federation checkout/checkin

Revision ID: 20260703_000000_add_account_transfers
Revises: 20260702_000000_add_account_owner_instance
Create Date: 2026-07-03

Durable, nonce-keyed handshake state for the instance-federation
checkout/checkin protocol (see openspec instance-federation design.md).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260703_000000_add_account_transfers"
down_revision = "20260702_000000_add_account_owner_instance"
branch_labels = None
depends_on = None


_ACCOUNT_TRANSFER_DIRECTION = sa.Enum(
    "checkout",
    "checkin",
    name="account_transfer_direction",
)
_ACCOUNT_TRANSFER_STATE = sa.Enum(
    "pending",
    "settled",
    name="account_transfer_state",
)


def _has_table(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "account_transfers"):
        return

    op.create_table(
        "account_transfers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("direction", _ACCOUNT_TRANSFER_DIRECTION, nullable=False),
        sa.Column("counterparty_instance_id", sa.String(), nullable=False),
        sa.Column("state", _ACCOUNT_TRANSFER_STATE, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("nonce", name="uq_account_transfers_nonce"),
    )
    op.create_index(
        "idx_account_transfers_account_direction_state",
        "account_transfers",
        ["account_id", "direction", "state"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "account_transfers"):
        op.drop_index("idx_account_transfers_account_direction_state", table_name="account_transfers")
        op.drop_table("account_transfers")
    _ACCOUNT_TRANSFER_DIRECTION.drop(bind, checkfirst=True)
    _ACCOUNT_TRANSFER_STATE.drop(bind, checkfirst=True)
