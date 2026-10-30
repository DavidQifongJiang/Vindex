"""Upload identities, processing deadlines, and operational health.

Revision ID: 20260905_0005
Revises: 20260904_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20260905_0005"
down_revision = "20260904_0004"
branch_labels = depends_on = None


def upgrade():
    for table in ("pipeline_jobs", "video_search_chunks"):
        op.add_column(table, sa.Column("deadline_at", sa.DateTime()))
        op.add_column(table, sa.Column("progress_at", sa.DateTime()))
        op.create_index(f"ix_{table}_deadline_at", table, ["deadline_at"])
        op.execute(sa.text(f"UPDATE {table} SET deadline_at = CURRENT_TIMESTAMP WHERE status = 'processing'"))
    op.create_table("upload_requests",
        sa.Column("owner_user_id", sa.String(), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("video_id", sa.String(), nullable=False, unique=True),
        sa.Column("processing_token", sa.String()), sa.Column("raw_path", sa.Text()),
        sa.Column("expires_at", sa.DateTime()), sa.Column("status", sa.String(), nullable=False),
        sa.Column("response", sa.JSON()), sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table("operation_states", sa.Column("name", sa.String(), primary_key=True),
        sa.Column("updated_at", sa.DateTime()), sa.Column("succeeded_at", sa.DateTime()),
        sa.Column("error", sa.Text()), sa.Column("details", sa.JSON()))


def downgrade():
    op.drop_table("operation_states")
    op.drop_table("upload_requests")
    for table in ("pipeline_jobs", "video_search_chunks"):
        op.drop_index(f"ix_{table}_deadline_at", table_name=table)
        op.drop_column(table, "progress_at")
        op.drop_column(table, "deadline_at")
