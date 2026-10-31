"""Create initial Vindex schema.

Revision ID: 20260602_0001
Revises:
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "20260602_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id VARCHAR NOT NULL,
            email VARCHAR,
            name VARCHAR,
            picture_url TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            updated_at TIMESTAMP WITHOUT TIME ZONE,
            PRIMARY KEY (user_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id VARCHAR NOT NULL,
            owner_user_id VARCHAR,
            title VARCHAR,
            visibility VARCHAR,
            filename VARCHAR,
            status VARCHAR,
            raw_path TEXT,
            processed_path TEXT,
            audio_path TEXT,
            transcript_path TEXT,
            segments_path TEXT,
            embedding_path TEXT,
            audio_status VARCHAR,
            transcript_status VARCHAR,
            segments_status VARCHAR,
            embedding_status VARCHAR,
            error_message TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            updated_at TIMESTAMP WITHOUT TIME ZONE,
            PRIMARY KEY (video_id)
        )
        """
    )

    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS picture_url TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE")

    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS title VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS visibility VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS filename VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS status VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS raw_path TEXT")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_path TEXT")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS audio_path TEXT")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS transcript_path TEXT")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS segments_path TEXT")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS embedding_path TEXT")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS audio_status VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS transcript_status VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS segments_status VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS embedding_status VARCHAR")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS error_message TEXT")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE")

    op.execute("UPDATE videos SET visibility = 'private' WHERE visibility IS NULL")
    op.execute("UPDATE videos SET owner_user_id = 'dev-user' WHERE owner_user_id IS NULL")

    op.execute("CREATE INDEX IF NOT EXISTS ix_users_user_id ON users (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_videos_video_id ON videos (video_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_videos_owner_user_id ON videos (owner_user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_videos_owner_user_id")
    op.execute("DROP INDEX IF EXISTS ix_videos_video_id")
    op.execute("DROP INDEX IF EXISTS ix_users_email")
    op.execute("DROP INDEX IF EXISTS ix_users_user_id")
    op.execute("DROP TABLE IF EXISTS videos")
    op.execute("DROP TABLE IF EXISTS users")
