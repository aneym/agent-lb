from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261026_200000_add_t3_thread_registry"
down_revision = "20261026_090000_add_postgres_quota_seek_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "t3_threads" not in tables:
        op.create_table(
            "t3_threads",
            sa.Column("host", sa.String(32), nullable=False),
            sa.Column("thread_id", sa.Text(), nullable=False),
            sa.Column("project_id", sa.String(64), nullable=True),
            sa.Column("project_title", sa.Text(), nullable=True),
            sa.Column("workspace_root", sa.Text(), nullable=True),
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column("provider_instance", sa.String(64), nullable=True),
            sa.Column("model", sa.String(64), nullable=True),
            sa.Column("parent_thread_id", sa.Text(), nullable=True),
            sa.Column("root_thread_id", sa.Text(), nullable=False),
            sa.Column("relationship", sa.String(32), nullable=True),
            sa.Column("created_by", sa.String(16), nullable=True),
            sa.Column("creation_source", sa.String(16), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("archived_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("host", "thread_id"),
        )
        op.create_index("ix_t3_threads_root", "t3_threads", ["host", "root_thread_id"])
    if "t3_native_sessions" not in tables:
        op.create_table(
            "t3_native_sessions",
            sa.Column("native_id", sa.String(64), nullable=False),
            sa.Column("host", sa.String(32), nullable=False),
            sa.Column("thread_id", sa.Text(), nullable=False),
            sa.Column("driver", sa.String(32), nullable=False),
            sa.Column("provider_instance", sa.String(64), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("native_id"),
        )
        op.create_index("ix_t3_native_sessions_thread", "t3_native_sessions", ["host", "thread_id"])


def downgrade() -> None:
    op.drop_table("t3_native_sessions")
    op.drop_table("t3_threads")
