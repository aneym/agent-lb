"""Add nullable room and upstream unified utilization to request logs.

Revision ID: 20261003_190000_add_request_log_room_unified_util
Revises: 20261001_180000_add_request_log_caller_seat
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261003_190000_add_request_log_room_unified_util"
down_revision = "20261001_180000_add_request_log_caller_seat"
branch_labels = None
depends_on = None

_COLUMNS = (("room", sa.String(128)), ("unified_5h_utilization", sa.Float()), ("unified_7d_utilization", sa.Float()))


def upgrade() -> None:
    # Re-runnable if an older revision is remapped and this revision replays.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # An ALTER waiting for ACCESS EXCLUSIVE must not queue live inserts behind it.
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        for name, column_type in _COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # An ALTER waiting for ACCESS EXCLUSIVE must not queue live inserts behind it.
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        for name, _ in reversed(_COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
