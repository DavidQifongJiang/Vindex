from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()


class Video(Base):
    __tablename__ = "videos"

    video_id = Column(String, primary_key=True, index=True)
    owner_user_id = Column(String, index=True)
    title = Column(String)
    visibility = Column(String, default="private")
    filename = Column(String)
    status = Column(String)

    raw_path = Column(Text)
    processed_path = Column(Text)
    audio_path = Column(Text)
    transcript_path = Column(Text)
    segments_path = Column(Text)
    embedding_path = Column(Text)

    audio_status = Column(String)
    transcript_status = Column(String)
    segments_status = Column(String)
    embedding_status = Column(String)

    error_message = Column(Text, nullable=True)
    processing_attempt_count = Column(Integer, default=0)
    max_processing_attempts = Column(Integer, default=3)
    last_error_code = Column(String, nullable=True)
    last_error_type = Column(String, nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    processing_owner = Column(String, nullable=True)
    processing_token = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class VideoSearchChunk(Base):
    __tablename__ = "video_search_chunks"

    video_id = Column(String, primary_key=True, index=True)
    chunk_index = Column(Integer, primary_key=True)
    status = Column(String, index=True)

    start_seconds = Column(Float)
    end_seconds = Column(Float)
    audio_start_seconds = Column(Float)
    audio_end_seconds = Column(Float)

    audio_path = Column(Text)
    transcript_path = Column(Text)
    segments_path = Column(Text)
    embedding_path = Column(Text)

    segment_count = Column(Integer, default=0)
    transcript_character_count = Column(Integer, default=0)

    audio_extraction_seconds = Column(Float)
    s3_audio_upload_seconds = Column(Float)
    s3_audio_download_seconds = Column(Float)
    whisper_model_load_or_get_seconds = Column(Float)
    whisper_transcription_seconds = Column(Float)
    embedding_model_load_or_get_seconds = Column(Float)
    embedding_generation_seconds = Column(Float)
    qdrant_upsert_seconds = Column(Float)
    s3_artifact_write_seconds = Column(Float)
    processing_seconds = Column(Float)

    processing_attempt_count = Column(Integer, default=0)
    max_processing_attempts = Column(Integer, default=3)
    processing_owner = Column(String, nullable=True)
    last_error_code = Column(String, nullable=True)
    last_error_type = Column(String, nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True, index=True)
    email = Column(String, index=True)
    name = Column(String)
    picture_url = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
