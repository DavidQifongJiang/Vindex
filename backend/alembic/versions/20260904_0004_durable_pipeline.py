"""Add durable pipeline jobs and fenced chunk leases.

Revision ID: 20260904_0004
Revises: 20260822_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0004"
down_revision = "20260822_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("thumbnail_path", sa.Text()))
    for name in ("processing_token", "artifact_token"):
        op.add_column("video_search_chunks", sa.Column(name, sa.String()))
    for name in ("lease_expires_at", "dispatch_after"):
        op.add_column("video_search_chunks", sa.Column(name, sa.DateTime()))
        op.create_index(f"ix_video_search_chunks_{name}", "video_search_chunks", [name])
    op.create_table(
        "pipeline_jobs",
        sa.Column("video_id", sa.String(), primary_key=True),
        sa.Column("stage", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("processing_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_processing_attempts", sa.Integer(), nullable=False, server_default="3"),
        *[sa.Column(name, sa.String()) for name in (
            "processing_owner", "processing_token", "last_error_code", "last_error_type")],
        sa.Column("last_error_message", sa.Text()),
        *[sa.Column(name, sa.DateTime()) for name in (
            "lease_expires_at", "dispatch_after", "next_retry_at", "processing_started_at",
            "completed_at", "last_error_at", "created_at", "updated_at")],
    )
    for name in ("status", "lease_expires_at", "dispatch_after"):
        op.create_index(f"ix_pipeline_jobs_{name}", "pipeline_jobs", [name])
    # Drain old workers first; their un-tokened attempts cannot be resumed safely.
    op.execute("""
        UPDATE video_search_chunks SET status = 'retrying', next_retry_at = CURRENT_TIMESTAMP,
            processing_owner = NULL, processing_started_at = NULL
        WHERE status = 'processing'
    """)
    op.execute("""
        INSERT INTO pipeline_jobs (video_id, stage, status, created_at, updated_at)
        SELECT video_id, 'search', 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM videos WHERE status IN ('uploaded', 'processing', 'retrying')
            AND COALESCE(embedding_status, '') <> 'completed'
    """)
    op.execute("""
        INSERT INTO pipeline_jobs (video_id, stage, status, created_at, updated_at)
        SELECT video_id, 'playback', 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM videos WHERE status IN ('uploaded', 'processing', 'retrying') AND processed_path IS NULL
    """)


def downgrade():
    op.drop_table("pipeline_jobs")
    for name in ("dispatch_after", "lease_expires_at", "artifact_token", "processing_token"):
        if name in {"dispatch_after", "lease_expires_at"}:
            op.drop_index(f"ix_video_search_chunks_{name}", table_name="video_search_chunks")
        op.drop_column("video_search_chunks", name)
    op.drop_column("videos", "thumbnail_path")
