from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261017_180000_add_request_log_attribution"
down_revision = "20261017_120000_add_stripped_tools"
branch_labels = None
depends_on = None

_COLUMNS = (("lane", sa.String(128)), ("job_id", sa.String(64)), ("dispatch_id", sa.String(64)))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        for name, column_type in _COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        for name, _ in reversed(_COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
