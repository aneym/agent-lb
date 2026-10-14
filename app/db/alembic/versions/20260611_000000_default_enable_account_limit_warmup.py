

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260611_000000_default_enable_account_limit_warmup"
down_revision = "20260610_180000_add_seed_target_offset_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.alter_column(
            "limit_warmup_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.alter_column(
            "limit_warmup_enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )
