"""Track retry backoff for continuous Anthropic limit warmups.

Revision ID: 20260923_000000_add_limit_warmup_retry_count
Revises: 20260918_000000_add_team_members
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_000000_add_limit_warmup_retry_count"
down_revision = "20260918_000000_add_team_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Re-runnable: a legacy revision id remaps to an older ancestor and replays this.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("account_limit_warmups")}
    if "retry_count" in columns:
        return
    with op.batch_alter_table("account_limit_warmups") as batch_op:
        batch_op.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("account_limit_warmups") as batch_op:
        batch_op.drop_column("retry_count")
