from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261120_010000_add_team_pool_share_accounts"
down_revision = "20261113_220000_add_request_log_thread_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("team_members")}
    if "pool_share_account_ids" not in columns:
        with op.batch_alter_table("team_members") as batch_op:
            batch_op.add_column(sa.Column("pool_share_account_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("team_members")}
    if "pool_share_account_ids" in columns:
        with op.batch_alter_table("team_members") as batch_op:
            batch_op.drop_column("pool_share_account_ids")
