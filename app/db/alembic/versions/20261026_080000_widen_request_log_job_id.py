from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261026_080000_widen_request_log_job_id"
down_revision = "20261023_120000_add_requested_model_alias"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "request_logs",
        "job_id",
        existing_type=sa.String(64),
        type_=sa.String(160),
        existing_nullable=True,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "request_logs",
        "job_id",
        existing_type=sa.String(160),
        type_=sa.String(64),
        existing_nullable=True,
    )
