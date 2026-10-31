from sqlalchemy import Column, DateTime, Float, Integer, String, Text, JSON, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()


class Video(Base):
    __tablename__ = "videos"

    video_id = Column(String, primary_key=True, index=True)
    owner_user_id = Column(String, index=True)
    current_search_revision_id = Column(String)
    title = Column(String)
    visibility = Column(String, default="private")
    filename = Column(String)
    status = Column(String)

    thumbnail_path = Column(Text)
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

    processing_token = Column(String, nullable=True)
    artifact_token = Column(String, nullable=True)
    deadline_at = Column(DateTime, index=True)
    progress_at = Column(DateTime)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    dispatch_after = Column(DateTime, nullable=True, index=True)

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


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"

    video_id = Column(String, primary_key=True)
    stage = Column(String, primary_key=True)
    deadline_at = Column(DateTime, index=True)
    progress_at = Column(DateTime)
    status = Column(String, nullable=False, default="pending", index=True)
    processing_attempt_count = Column(Integer, nullable=False, default=0)
    max_processing_attempts = Column(Integer, nullable=False, default=3)
    processing_owner = Column(String)
    processing_token = Column(String)
    lease_expires_at = Column(DateTime, index=True)
    dispatch_after = Column(DateTime, index=True)
    next_retry_at = Column(DateTime)
    processing_started_at = Column(DateTime)
    completed_at = Column(DateTime)
    last_error_code = Column(String)
    last_error_type = Column(String)
    last_error_message = Column(Text)
    last_error_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class UploadRequest(Base):
    __tablename__ = "upload_requests"

    owner_user_id = Column(String, primary_key=True)
    idempotency_key = Column(String(128), primary_key=True)
    fingerprint = Column(String(64), nullable=False)
    video_id = Column(String, nullable=False, unique=True)
    processing_token = Column(String)
    raw_path = Column(Text)
    expires_at = Column(DateTime)
    status = Column(String, nullable=False)
    response = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class OperationState(Base):
    __tablename__ = "operation_states"

    name = Column(String, primary_key=True)
    updated_at = Column(DateTime)
    succeeded_at = Column(DateTime)
    error = Column(Text)
    details = Column(JSON)


class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True, index=True)
    email = Column(String, index=True)
    name = Column(String)
    picture_url = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class SearchRevision(Base):
    __tablename__ = "search_revisions"
    revision_id = Column(String, primary_key=True)
    video_id = Column(String, nullable=False, index=True)
    source_key = Column(String(64), nullable=False, unique=True)
    segments_path = Column(Text, nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)
    passage_count = Column(Integer, nullable=False)
    embedding_path = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SearchGeneration(Base):
    __tablename__ = "search_generations"
    generation_id = Column(String, primary_key=True)
    backend = Column(String, nullable=False)
    target = Column(String, nullable=False)
    settings = Column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("backend", "target"),
        CheckConstraint("backend IN ('lexical', 'semantic')", name="ck_generation_backend"))


class SearchConfiguration(Base):
    __tablename__ = "search_configuration"
    name = Column(String, primary_key=True)
    generations = Column(JSON, nullable=False)


class SearchProjection(Base):
    __tablename__ = "search_projections"
    job_id = Column(String, primary_key=True)
    video_id = Column(String, nullable=False, index=True)
    revision_id = Column(String, ForeignKey("search_revisions.revision_id"), nullable=False)
    generation_id = Column(String, ForeignKey("search_generations.generation_id"), nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    token = Column(String)
    accepted_attempt_id = Column(String)
    lease_expires_at = Column(DateTime)
    deadline_at = Column(DateTime)
    next_retry_at = Column(DateTime)
    dispatch_after = Column(DateTime)
    verified_at = Column(DateTime)
    error_code = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("video_id", "revision_id", "generation_id"),
        CheckConstraint("status IN ('pending','indexing','retrying','ready','failed','obsolete')", name="ck_projection_status"),
        CheckConstraint("attempt_count >= 0 AND max_attempts > 0", name="ck_projection_attempts"))


class SearchAttempt(Base):
    __tablename__ = "search_attempts"
    attempt_id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("search_projections.job_id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    deadline_at = Column(DateTime, nullable=False)
    cleanup_after = Column(DateTime, nullable=False, index=True)
    cleaned_at = Column(DateTime)
    error_code = Column(String)


class SearchDeletion(Base):
    __tablename__ = "search_deletions"
    # Deliberately no Video FK: cleanup identity survives deletion of the video.
    video_id = Column(String, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
