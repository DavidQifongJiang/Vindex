from sqlalchemy import Column, String, DateTime, Text
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
