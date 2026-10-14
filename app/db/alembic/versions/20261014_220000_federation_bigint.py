from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261014_220000_federation_bigint"
down_revision = "20261014_050000_add_additional_latest_indexes"
branch_labels = None
depends_on = None

_COUNTERS = ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "session_count")


def upgrade() -> None:
    with op.batch_alter_table("federation_usage_daily") as batch:
        for column in _COUNTERS:
            batch.alter_column(column, existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("federation_usage_daily") as batch:
        for column in _COUNTERS:
            batch.alter_column(column, existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
