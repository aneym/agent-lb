"""add seed_target_offset_minutes to quota_planner_settings

Revision ID: 20260610_180000_add_seed_target_offset_minutes
Revises: 20260609_150000_add_account_subscription_ledger
Create Date: 2026-06-10 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260610_180000_add_seed_target_offset_minutes"
down_revision = "20260609_150000_add_account_subscription_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quota_planner_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "seed_target_offset_minutes",
                sa.Integer(),
                nullable=False,
                server_default="120",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("quota_planner_settings") as batch_op:
        batch_op.drop_column("seed_target_offset_minutes")
