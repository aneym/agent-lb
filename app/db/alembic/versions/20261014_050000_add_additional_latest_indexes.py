from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261014_050000_add_additional_latest_indexes"
down_revision = "20261014_040000_add_unified_header_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    for suffix, column in (("key", "quota_key"), ("limit", "lower(limit_name)"), ("feature", "lower(metered_feature)")):
        op.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS ix_additional_latest_{suffix} "
                f"ON additional_usage_history (account_id, window, {column}, "
                "recorded_at DESC, used_percent DESC, id DESC)"
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for suffix in ("key", "limit", "feature"):
        op.execute(sa.text(f"DROP INDEX IF EXISTS ix_additional_latest_{suffix}"))
