import sqlalchemy as sa
from alembic import op

revision = "20261101_180000_add_request_log_daily"
down_revision = "20261029_220000_add_verifier_receipts"
branch_labels = None
depends_on = None

_SUMS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "output_or_reasoning_tokens",
    "cached_input_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "reasoning_tokens",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    if sa.inspect(bind).has_table("request_log_daily"):
        return
    op.create_table(
        "request_log_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("archive_id", sa.String(64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("api_key_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("request_kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("superseded", sa.Boolean(), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in _SUMS),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms_sum", sa.BigInteger(), nullable=False),
        sa.Column("latency_count", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_request_log_daily_day", "request_log_daily", ["day"])
    op.create_index("ix_request_log_daily_archive", "request_log_daily", ["archive_id"])
    op.create_index("ix_request_log_daily_account", "request_log_daily", ["account_id"])
    op.create_index("ix_request_log_daily_api_key", "request_log_daily", ["api_key_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")
    if sa.inspect(bind).has_table("request_log_daily"):
        op.drop_table("request_log_daily")
