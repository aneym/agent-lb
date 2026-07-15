"""add request logs session time index

Revision ID: 20260715_160000_add_request_logs_session_time_index
Revises: 20260703_000000_add_account_transfers
Create Date: 2026-07-15 16:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260715_160000_add_request_logs_session_time_index"
down_revision = "20260703_000000_add_account_transfers"
branch_labels = None
depends_on = None

_REQUEST_LOGS_TABLE = "request_logs"
_SESSION_TIME_INDEX = "idx_logs_session_time"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_REQUEST_LOGS_TABLE):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes(_REQUEST_LOGS_TABLE)}
    if _SESSION_TIME_INDEX not in existing_indexes:
        op.create_index(
            _SESSION_TIME_INDEX,
            _REQUEST_LOGS_TABLE,
            ["session_id", sa.text("requested_at DESC")],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_REQUEST_LOGS_TABLE):
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes(_REQUEST_LOGS_TABLE)}
    if _SESSION_TIME_INDEX in existing_indexes:
        op.drop_index(_SESSION_TIME_INDEX, table_name=_REQUEST_LOGS_TABLE)
