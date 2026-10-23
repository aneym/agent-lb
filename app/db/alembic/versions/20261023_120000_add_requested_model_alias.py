from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261023_120000_add_requested_model_alias"
down_revision = "20261021_130000_add_request_logs_job_time_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("requested_model_alias", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("request_logs", "requested_model_alias")
