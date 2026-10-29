from alembic import op

revision = "20261029_160000_add_request_log_factory_project_index"
down_revision = "20261027_120000_add_account_warmup_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_logs_factory_project_id "
            "ON request_logs (factory_project, id DESC) WHERE factory_project IS NOT NULL"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_logs_factory_project_id")
