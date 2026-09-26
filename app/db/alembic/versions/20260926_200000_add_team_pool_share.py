"""Add optional team member pool share limit.

Revision ID: 20260926_200000_add_team_pool_share
Revises: 20260925_020000_add_account_resume_schedules
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260926_200000_add_team_pool_share"
down_revision = "20260925_020000_add_account_resume_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("team_members", sa.Column("pool_share_percent", sa.Numeric(6, 3, asdecimal=False), nullable=True))


def downgrade() -> None:
    op.drop_column("team_members", "pool_share_percent")
