"""Add nullable caller identity fields to request logs.

Revision ID: 20260926_210000_add_request_log_identity
Revises: 20260926_200000_add_team_pool_share
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260926_210000_add_request_log_identity"
down_revision = "20260926_200000_add_team_pool_share"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("caller_user", 32),
    ("caller_user_source", 32),
    ("caller_machine", 48),
    ("caller_machine_source", 16),
)


def upgrade() -> None:
    # Re-runnable if an older revision is remapped and this revision replays.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # An ALTER waiting for ACCESS EXCLUSIVE must not queue live inserts behind it.
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        for name, length in _COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, sa.String(length), nullable=True))


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
