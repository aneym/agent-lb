

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260309_000000_request_logs_nullable_account_id"
down_revision = "20260308_000000_add_sqlite_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.alter_column("account_id", nullable=True)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM request_logs WHERE account_id IS NULL"))
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.alter_column("account_id", nullable=False)
