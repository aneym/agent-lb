from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261127_010000_oauth_flows"
down_revision = "20261126_003000_add_t3_environment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_flows",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("state_digest", sa.String(64), unique=True),
        sa.Column("secrets_encrypted", sa.LargeBinary),
        sa.Column("error_message", sa.Text),
        sa.Column("probe_status_code", sa.Integer),
        sa.Column("verification_deadline", sa.Float),
        sa.Column("started_at", sa.Float, nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("interval_seconds", sa.Integer),
        sa.Column("poll_after", sa.Float),
        sa.Column("poll_owner", sa.String(64)),
    )
    op.create_index("ix_oauth_flows_expires_at", "oauth_flows", ["expires_at"])


def downgrade() -> None:
    op.drop_table("oauth_flows")
