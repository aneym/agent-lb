from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261016_180000_exchange_intent_auto_resets"
down_revision = "20261014_220000_federation_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account_exchange_intents",
        sa.Column("auto_resets", sa.SmallInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("account_exchange_intents", "auto_resets")
