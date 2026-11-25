from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261126_003000_add_t3_environment"
down_revision = "20261120_020000_add_team_pool_share_by_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    if "environment_id" not in {column["name"] for column in sa.inspect(bind).get_columns("t3_threads")}:
        op.add_column("t3_threads", sa.Column("environment_id", sa.String(96), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("t3_threads") as batch_op:
        batch_op.drop_column("environment_id")
