"""Add processing retry state.

Revision ID: 20260721_0002
Revises: 20260602_0001
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "20260721_0002"
down_revision: Union[str, None] = "20260602_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processing_attempt_count INTEGER DEFAULT 0")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS max_processing_attempts INTEGER DEFAULT 3")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_error_code VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_error_type VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_error_message TEXT")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processing_owner VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processing_token VARCHAR")

    op.execute("UPDATE videos SET processing_attempt_count = 0 WHERE processing_attempt_count IS NULL")
    op.execute("UPDATE videos SET max_processing_attempts = 3 WHERE max_processing_attempts IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_videos_status ON videos (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_videos_processing_token ON videos (processing_token)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_videos_processing_token")
    op.execute("DROP INDEX IF EXISTS ix_videos_status")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS processing_token")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS processing_owner")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS processing_started_at")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS next_retry_at")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS last_error_at")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS last_error_message")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS last_error_type")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS last_error_code")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS max_processing_attempts")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS processing_attempt_count")
