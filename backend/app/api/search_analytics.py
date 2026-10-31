"""Opt-in interactions and explicitly authorized developer read views."""
import os
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import ValidationError
from app.services.auth_service import get_current_user
from app.services.search_events import Interaction, enabled, telemetry_allowed
from app.db import search_event_repository as repository

class BoundedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request):
            if request.method == "POST":
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 8192:
                        raise HTTPException(413, detail="interaction_too_large")
                request._body = bytes(body)
            return await handler(request)
        return bounded


router = APIRouter(route_class=BoundedRoute)


def require_events():
    if not enabled("SEARCH_EVENTS_INTAKE_ENABLED") or not telemetry_allowed():
        raise HTTPException(404, detail="interaction_capture_disabled")


@router.post("/search/{search_id}/interactions", status_code=202)
def interaction(search_id: UUID, body: Interaction, user=Depends(get_current_user)):
    require_events()
    try:
        repository.accept_interaction(user.user_id, str(search_id), body)
    except PermissionError:
        raise HTTPException(404, detail="search_context_unavailable")
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc))
    except Exception:
        raise HTTPException(503, detail="feedback_not_saved", headers={"Retry-After": "1"})
    return {"accepted": True, "event_id": body.event_id}


@router.delete("/search/{search_id}/sharing", status_code=204)
def withdraw(search_id: UUID, user=Depends(get_current_user)):
    # Revocation remains available when new intake is disabled during rollback.
    if not telemetry_allowed():
        raise HTTPException(404)
    try:
        repository.withdraw(user.user_id, str(search_id))
    except PermissionError:
        raise HTTPException(404, detail="search_context_unavailable")


@router.get("/search-analytics")
def analytics(cursor: UUID | None = None, user=Depends(get_current_user)):
    if not enabled("SEARCH_EVENTS_DASHBOARD_ENABLED") or not telemetry_allowed():
        raise HTTPException(404)
    reviewers = {s.strip() for s in os.getenv("SEARCH_EVENTS_REVIEWERS", "").split(",") if s.strip()}
    if user.user_id not in reviewers:
        raise HTTPException(403)
    return repository.dashboard(str(cursor) if cursor else "")


@router.delete("/search-sharing", status_code=204)
def withdraw_all(user=Depends(get_current_user)):
    if not telemetry_allowed():
        raise HTTPException(404)
    repository.withdraw_all(user.user_id)
