"""Record Anthropic cache writes with their TTL tier."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261005_150000_add_cache_creation_tier"
down_revision = "20261003_190000_add_request_log_room_unified_util"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    if "cache_creation_tier" not in existing:
        op.add_column("request_logs", sa.Column("cache_creation_tier", sa.String(2), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("request_logs", "cache_creation_tier")
