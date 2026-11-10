import sqlalchemy as sa
from alembic import op

revision = "20261110_120000_add_anthropic_usage_limits"
down_revision = "20261101_180000_add_request_log_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    if sa.inspect(bind).has_table("anthropic_usage_limits"):
        return
    op.create_table(
        "anthropic_usage_limits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_anthropic_usage_limits_account_recorded",
        "anthropic_usage_limits",
        ["account_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_anthropic_usage_limits_account_recorded", table_name="anthropic_usage_limits")
    op.drop_table("anthropic_usage_limits")
