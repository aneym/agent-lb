import sqlalchemy as sa
from alembic import op

revision = "20261027_120000_add_account_warmup_policy"
down_revision = "20261023_000000_factory_usage_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "warmup_until" not in columns:
        op.add_column("accounts", sa.Column("warmup_until", sa.DateTime(), nullable=True))
    if "warmup_capacity_percent" not in columns:
        op.add_column("accounts", sa.Column("warmup_capacity_percent", sa.Float(), nullable=True))
    if "warmup_max_concurrent" not in columns:
        op.add_column("accounts", sa.Column("warmup_max_concurrent", sa.Integer(), nullable=True))
    if "warmup_owner_instance" not in columns:
        op.add_column("accounts", sa.Column("warmup_owner_instance", sa.String(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}
    for name in ("warmup_owner_instance", "warmup_max_concurrent", "warmup_capacity_percent", "warmup_until"):
        if name in columns:
            op.drop_column("accounts", name)
