"""Add durable account resume schedules.

Revision ID: 20260925_020000_add_account_resume_schedules
Revises: 20260925_010000_add_routing_policy_versions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_020000_add_account_resume_schedules"
down_revision = "20260925_010000_add_routing_policy_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_resume_schedules",
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("resume_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("account_resume_schedules")
