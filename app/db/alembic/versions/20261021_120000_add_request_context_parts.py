import sqlalchemy as sa
from alembic import op

revision = "20261021_120000_add_request_context_parts"
down_revision = "20261017_180000_add_request_log_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    if sa.inspect(bind).has_table("request_context_parts"):
        return
    op.create_table(
        "request_context_parts",
        sa.Column(
            "request_log_id", sa.Integer(), sa.ForeignKey("request_logs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("body_schema", sa.String(32), nullable=True),
        sa.Column("body_bytes", sa.BigInteger(), nullable=False),
        *(
            sa.Column(name, sa.BigInteger(), nullable=True)
            for name in (
                "system_chars",
                "tools_chars",
                "history_chars",
                "tool_result_chars",
                "tool_count",
                "message_count",
                "image_count",
            )
        ),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    if sa.inspect(bind).has_table("request_context_parts"):
        op.drop_table("request_context_parts")
