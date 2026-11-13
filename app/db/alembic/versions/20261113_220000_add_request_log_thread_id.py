from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261113_220000_add_request_log_thread_id"
down_revision = "20261110_120000_add_anthropic_usage_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    if "thread_id" not in existing:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(sa.Column("thread_id", sa.String(256), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    if "thread_id" in existing:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("thread_id")
