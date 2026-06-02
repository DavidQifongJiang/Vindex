from datetime import datetime
from app.db.models import Video


def create_video(db, video_data: dict):
    video = Video(**video_data)

    db.add(video)
    db.commit()
    db.refresh(video)

    return video


def get_video(db, video_id: str):
    return db.query(Video).filter(Video.video_id == video_id).first()


def list_videos(db, owner_user_id: str, limit: int = 25):
    return (
        db.query(Video)
        .filter(Video.owner_user_id == owner_user_id)
        .order_by(Video.created_at.desc())
        .limit(limit)
        .all()
    )


def list_public_videos(db, limit: int = 25):
    return (
        db.query(Video)
        .filter(Video.visibility == "public")
        .order_by(Video.created_at.desc())
        .limit(limit)
        .all()
    )


def update_video(db, video_id: str, updates: dict):
    video = get_video(db, video_id)

    if video is None:
        return None

    for key, value in updates.items():
        setattr(video, key, value)

    video.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(video)

    return video
