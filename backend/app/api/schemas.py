"""Public response contracts for video endpoints.

Database rows carry storage keys, lease tokens and worker identities that must
never reach a browser. Endpoints build these models explicitly instead of
serializing ``Video`` rows, so new columns stay private by default.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class VideoSummary(BaseModel):
    video_id: str
    title: str | None = None
    visibility: str | None = None
    filename: str | None = None
    status: str | None = None
    audio_status: str | None = None
    transcript_status: str | None = None
    segments_status: str | None = None
    embedding_status: str | None = None
    playable: bool = False
    is_owner: bool = False
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class VideoStatusResponse(VideoSummary):
    keyword_search_enabled: bool = False


class VideoListResponse(BaseModel):
    videos: list[VideoSummary]


def video_summary(video, current_user=None, model=VideoSummary, **extra):
    is_owner = current_user is not None and video.owner_user_id == current_user.user_id
    return model(
        video_id=video.video_id,
        title=video.title,
        visibility=video.visibility,
        filename=video.filename,
        status=video.status,
        audio_status=video.audio_status,
        transcript_status=video.transcript_status,
        segments_status=video.segments_status,
        embedding_status=video.embedding_status,
        playable=bool(video.processed_path),
        is_owner=is_owner,
        # Failure text can include internal detail; only the owner needs it.
        error_message=video.error_message if is_owner else None,
        created_at=video.created_at,
        updated_at=video.updated_at,
        **extra,
    )


class CreateUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=500)
    visibility: str = "private"
    size_bytes: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=100)


class PresignedPost(BaseModel):
    method: Literal["POST"] = "POST"
    url: str
    fields: dict[str, str]


class UploadAccepted(BaseModel):
    video_id: str
    title: str
    visibility: str
    filename: str
    status: str
    idempotency_key: str


class CreateUploadResponse(BaseModel):
    video_id: str
    idempotency_key: str
    # None when this key already finished uploading; ``completed`` holds the result.
    upload: PresignedPost | None = None
    expires_in_seconds: int | None = None
    completed: UploadAccepted | None = None
