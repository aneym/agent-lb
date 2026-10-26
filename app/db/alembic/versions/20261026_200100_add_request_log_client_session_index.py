from alembic import op

revision = "20261026_200100_add_request_log_client_session_index"
down_revision = "20261026_200000_add_t3_thread_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_logs_client_session_time "
            "ON request_logs (client_session_id, requested_at DESC) WHERE client_session_id IS NOT NULL"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_logs_client_session_time")
