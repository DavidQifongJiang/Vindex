from datetime import datetime

from app.db.models import User


def get_user(db, user_id: str):
    return db.query(User).filter(User.user_id == user_id).first()


def upsert_user(db, auth_user):
    user = get_user(db, auth_user.user_id)

    if user is None:
        user = User(
            user_id=auth_user.user_id,
            email=auth_user.email,
            name=auth_user.name,
            picture_url=auth_user.picture_url,
        )
        db.add(user)
    else:
        user.email = auth_user.email
        user.name = auth_user.name
        user.picture_url = auth_user.picture_url
        user.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(user)

    return user
