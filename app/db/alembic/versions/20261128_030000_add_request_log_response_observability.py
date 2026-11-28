from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261128_030000_add_request_log_response_observability"
down_revision = "20261127_010000_oauth_flows"
branch_labels = None
depends_on = None

_COLUMNS = ("response_model", "stop_reason", "stop_details_category")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    missing = [name for name in _COLUMNS if name not in existing]
    if missing:
        with op.batch_alter_table("request_logs") as batch_op:
            for name in missing:
                batch_op.add_column(sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    present = [name for name in reversed(_COLUMNS) if name in existing]
    if present:
        with op.batch_alter_table("request_logs") as batch_op:
            for name in present:
                batch_op.drop_column(name)
