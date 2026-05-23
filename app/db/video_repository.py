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