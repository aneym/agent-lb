from alembic import op

revision = "20261021_130000_add_request_logs_job_time_index"
down_revision = "20261021_120000_add_request_context_parts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_logs_job_time "
            "ON request_logs (job_id, requested_at DESC) WHERE job_id IS NOT NULL"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_logs_job_time")
