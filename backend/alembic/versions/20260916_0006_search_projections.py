"""Durable, independently publishable search projections."""
from alembic import op
import sqlalchemy as sa

revision = "20260916_0006"
down_revision = "20260905_0005"
branch_labels = depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("current_search_revision_id", sa.String()))
    op.create_table("search_revisions",
        sa.Column("revision_id", sa.String(), primary_key=True),
        sa.Column("video_id", sa.String(), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False, unique=True),
        sa.Column("segments_path", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("passage_count", sa.Integer(), nullable=False),
        sa.Column("embedding_path", sa.Text()), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_search_revisions_video_id", "search_revisions", ["video_id"])
    op.create_table("search_generations",
        sa.Column("generation_id", sa.String(), primary_key=True),
        sa.Column("backend", sa.String(), nullable=False), sa.Column("target", sa.String(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False), sa.UniqueConstraint("backend", "target"),
        sa.CheckConstraint("backend IN ('lexical', 'semantic')", name="ck_generation_backend"))
    op.create_table("search_configuration", sa.Column("name", sa.String(), primary_key=True),
        sa.Column("generations", sa.JSON(), nullable=False))
    op.create_table("search_projections",
        sa.Column("job_id", sa.String(), primary_key=True), sa.Column("video_id", sa.String(), nullable=False),
        sa.Column("revision_id", sa.String(), sa.ForeignKey("search_revisions.revision_id"), nullable=False),
        sa.Column("generation_id", sa.String(), sa.ForeignKey("search_generations.generation_id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False), sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False), sa.Column("token", sa.String()),
        sa.Column("accepted_attempt_id", sa.String()), sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column("deadline_at", sa.DateTime()), sa.Column("next_retry_at", sa.DateTime()),
        sa.Column("dispatch_after", sa.DateTime()), sa.Column("verified_at", sa.DateTime()),
        sa.Column("error_code", sa.String()), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("video_id", "revision_id", "generation_id"),
        sa.CheckConstraint("status IN ('pending','indexing','retrying','ready','failed','obsolete')", name="ck_projection_status"),
        sa.CheckConstraint("attempt_count >= 0 AND max_attempts > 0", name="ck_projection_attempts"))
    for field in ("video_id", "status"):
        op.create_index(f"ix_search_projections_{field}", "search_projections", [field])
    op.create_table("search_attempts", sa.Column("attempt_id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("search_projections.job_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("cleanup_after", sa.DateTime(), nullable=False), sa.Column("cleaned_at", sa.DateTime()),
        sa.Column("error_code", sa.String()))
    for field in ("job_id", "cleanup_after"):
        op.create_index(f"ix_search_attempts_{field}", "search_attempts", [field])
    op.create_table("search_deletions", sa.Column("video_id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    for table in ("search_deletions", "search_attempts", "search_projections", "search_configuration",
                  "search_generations", "search_revisions"):
        op.drop_table(table)
    op.drop_column("videos", "current_search_revision_id")
