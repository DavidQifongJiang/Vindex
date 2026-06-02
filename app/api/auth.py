from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.user_repository import upsert_user
from app.services.auth_service import AuthUser, auth_required, get_current_user

router = APIRouter()


@router.get("/me")
def get_me(
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = upsert_user(db, current_user)

    return {
        "auth_required": auth_required(),
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "picture_url": user.picture_url,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }
