from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261017_120000_add_stripped_tools"
down_revision = "20261016_180000_exchange_intent_auto_resets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("stripped_tools", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("request_logs", "stripped_tools")
