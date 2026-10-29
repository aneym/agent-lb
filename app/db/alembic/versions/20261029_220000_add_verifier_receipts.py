from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_220000_add_verifier_receipts"
down_revision = "20261029_160000_add_request_log_factory_project_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "verifier_receipts" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "verifier_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("repo", sa.String(128), nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column("contract_hash", sa.String(128), nullable=False),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("seat", sa.String(64), nullable=True),
        sa.Column("job_id", sa.String(160), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_verifier_receipts_key", "verifier_receipts", ["repo", "head_sha", "contract_hash", "id"])


def downgrade() -> None:
    op.drop_index("ix_verifier_receipts_key", table_name="verifier_receipts")
    op.drop_table("verifier_receipts")
