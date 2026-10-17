from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260308_000000_add_sqlite_performance_indexes"
down_revision = "20260307_000000_add_api_key_enforcement_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_usage_window_account_latest
            ON usage_history (coalesce("window", 'primary'), account_id, recorded_at DESC, id DESC)
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_logs_requested_at_id
            ON request_logs (requested_at DESC, id DESC)
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_logs_requested_at_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_usage_window_account_latest"))
