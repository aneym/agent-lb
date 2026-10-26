from alembic import op

revision = "20261026_090000_add_postgres_quota_seek_indexes"
down_revision = "20261026_080000_widen_request_log_job_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for suffix, column in (
            ("key", "quota_key"),
            ("limit", "lower(limit_name)"),
            ("feature", "lower(metered_feature)"),
        ):
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_additional_latest_{suffix} "
                f'ON additional_usage_history (account_id, "window", {column}, '
                "recorded_at DESC, used_percent DESC, id DESC)"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for suffix in ("key", "limit", "feature"):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS ix_additional_latest_{suffix}")
