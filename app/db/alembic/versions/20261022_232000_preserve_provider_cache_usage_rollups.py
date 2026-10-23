"""Keep provider cache counters in account lifetime rollups."""

import sqlalchemy as sa
from alembic import op

revision = "20261022_232000_preserve_provider_cache_usage_rollups"
down_revision = "20261022_231000_add_oauth_provider_and_compatibility_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("cache_creation_tokens", "cache_read_tokens"):
        op.add_column(
            "account_usage_rollups", sa.Column(name, sa.BigInteger(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    for name in ("cache_read_tokens", "cache_creation_tokens"):
        op.drop_column("account_usage_rollups", name)
