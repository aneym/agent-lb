"""Record the client's own session id on request logs.

Revision ID: 20260925_000000_add_request_log_client_session_id
Revises: 20260923_000000_add_limit_warmup_retry_count
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_000000_add_request_log_client_session_id"
down_revision = "20260923_000000_add_limit_warmup_retry_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("request_logs")}
    if "client_session_id" in columns:
        return
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("client_session_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("client_session_id")
