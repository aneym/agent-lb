"""add account subscription ledger columns

Revision ID: 20260609_150000_add_account_subscription_ledger
Revises: 20260609_120000_merge_provider_and_useragent_heads
Create Date: 2026-06-09 15:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260609_150000_add_account_subscription_ledger"
down_revision = "20260609_120000_merge_provider_and_useragent_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("subscription_status", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("subscription_next_charge_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("subscription_current_period_end_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("subscription_amount", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("subscription_currency", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("subscription_last_verified_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("subscription_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("subscription_notes")
        batch_op.drop_column("subscription_last_verified_at")
        batch_op.drop_column("subscription_currency")
        batch_op.drop_column("subscription_amount")
        batch_op.drop_column("subscription_current_period_end_at")
        batch_op.drop_column("subscription_next_charge_at")
        batch_op.drop_column("subscription_status")
