"""Add observed routing policy versions and drafts.

Revision ID: 20260925_010000_add_routing_policy_versions
Revises: 20260925_000000_add_request_log_client_session_id
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_010000_add_routing_policy_versions"
down_revision = "20260925_000000_add_request_log_client_session_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routing_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.Integer(), unique=True, nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("routing_table_json", sa.Text(), nullable=False),
        sa.Column("decider_json", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("replay_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("routing_policy_versions")
