"""Preserve subscription quota response evidence."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261014_040000_add_unified_header_evidence"
down_revision = "20261005_150000_add_cache_creation_tier"
branch_labels = None
depends_on = None

_COLUMNS = (("unified_headers", sa.JSON()), ("quota_observed_at", sa.DateTime()))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch:
        for name, column_type in _COLUMNS:
            if name not in existing:
                batch.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch:
        for name, _ in reversed(_COLUMNS):
            if name in existing:
                batch.drop_column(name)
