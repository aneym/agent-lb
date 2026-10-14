

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260702_000000_add_account_owner_instance"
down_revision = "20260611_000000_default_enable_account_limit_warmup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("accounts"):
        return

    columns = {column["name"] for column in inspector.get_columns("accounts")}
    if "owner_instance" not in columns:
        op.add_column(
            "accounts",
            sa.Column("owner_instance", sa.String(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("accounts"):
        return

    columns = {column["name"] for column in inspector.get_columns("accounts")}
    if "owner_instance" in columns:
        op.drop_column("accounts", "owner_instance")
