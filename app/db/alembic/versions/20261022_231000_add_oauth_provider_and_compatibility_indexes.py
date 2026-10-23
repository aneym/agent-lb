"""Preserve provider identity in durable OAuth flows and align compatibility indexes."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261022_231000_add_oauth_provider_and_compatibility_indexes"
down_revision = "20261022_230000_merge_codex_lb_v124_and_fork_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("oauth_flow_states")}
    if "provider" not in columns:
        op.add_column("oauth_flow_states", sa.Column("provider", sa.String(), nullable=False, server_default="openai"))
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            invalid = (
                op.get_bind()
                .execute(
                    sa.text(
                        "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                        "WHERE c.relname = 'ix_additional_latest_key' AND NOT i.indisvalid"
                    )
                )
                .scalar()
            )
            if invalid:
                op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_additional_latest_key")
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_additional_latest_key "
                'ON additional_usage_history (account_id, "window", quota_key, '
                "recorded_at DESC, used_percent DESC, id DESC)"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_additional_latest_key")
    op.drop_column("oauth_flow_states", "provider")
