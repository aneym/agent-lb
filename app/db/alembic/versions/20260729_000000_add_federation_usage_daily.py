"""add federation usage daily

Revision ID: 20260729_000000_add_federation_usage_daily
Revises: 20260715_160000_add_request_logs_session_time_index
Create Date: 2026-07-29 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260729_000000_add_federation_usage_daily"
down_revision = "20260715_160000_add_request_logs_session_time_index"
branch_labels = None
depends_on = None

_TABLE = "federation_usage_daily"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("instance_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("session_count", sa.Integer(), nullable=False),
        sa.Column("last_request_at", sa.DateTime(), nullable=True),
        sa.Column("reported_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("instance_id", "account_id", "day"),
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)
