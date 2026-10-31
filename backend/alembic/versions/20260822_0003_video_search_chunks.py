"""Add video search chunks.

Revision ID: 20260822_0003
Revises: 20260721_0002
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op

revision: str = "20260822_0003"
down_revision: Union[str, None] = "20260721_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS video_search_chunks (
            video_id VARCHAR NOT NULL,
            chunk_index INTEGER NOT NULL,
            status VARCHAR,
            start_seconds FLOAT,
            end_seconds FLOAT,
            audio_start_seconds FLOAT,
            audio_end_seconds FLOAT,
            audio_path TEXT,
            transcript_path TEXT,
            segments_path TEXT,
            embedding_path TEXT,
            segment_count INTEGER DEFAULT 0,
            transcript_character_count INTEGER DEFAULT 0,
            audio_extraction_seconds FLOAT,
            s3_audio_upload_seconds FLOAT,
            s3_audio_download_seconds FLOAT,
            whisper_model_load_or_get_seconds FLOAT,
            whisper_transcription_seconds FLOAT,
            embedding_model_load_or_get_seconds FLOAT,
            embedding_generation_seconds FLOAT,
            qdrant_upsert_seconds FLOAT,
            s3_artifact_write_seconds FLOAT,
            processing_seconds FLOAT,
            processing_attempt_count INTEGER DEFAULT 0,
            max_processing_attempts INTEGER DEFAULT 3,
            processing_owner VARCHAR,
            last_error_code VARCHAR,
            last_error_type VARCHAR,
            last_error_message TEXT,
            last_error_at TIMESTAMP WITHOUT TIME ZONE,
            next_retry_at TIMESTAMP WITHOUT TIME ZONE,
            processing_started_at TIMESTAMP WITHOUT TIME ZONE,
            completed_at TIMESTAMP WITHOUT TIME ZONE,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            updated_at TIMESTAMP WITHOUT TIME ZONE,
            PRIMARY KEY (video_id, chunk_index)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_video_search_chunks_video_id ON video_search_chunks (video_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_video_search_chunks_status ON video_search_chunks (status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_video_search_chunks_status")
    op.execute("DROP INDEX IF EXISTS ix_video_search_chunks_video_id")
    op.execute("DROP TABLE IF EXISTS video_search_chunks")
