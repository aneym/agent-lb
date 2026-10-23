"""Join the stable upstream and agent-lb migration histories."""

revision = "20261022_230000_merge_codex_lb_v124_and_fork_heads"
down_revision = (
    "20261021_130000_add_request_logs_job_time_index",
    "20260816_000000_add_model_source_embeddings",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
