"""Authenticated first-page library search, independently gated from indexing."""
import asyncio
import os
from uuid import uuid4
import secrets
from time import monotonic
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from app.services.auth_service import AuthUser, get_current_user
from app.services.search_contracts import LibrarySearchQuery
from app.services.library_search import SearchError, submit_search
from app.services.search_observability import trace_id, span, record_response, render_metrics

router = APIRouter()


async def capture_outcome(body, owner, request_id, status, payload, elapsed):
    if not body.analytics_opt_in:
        return False
    try:
        from app.services.search_events import enabled, telemetry_allowed, completion, capture
        if not enabled("SEARCH_EVENTS_CAPTURE_ENABLED") or not telemetry_allowed():
            return False
        event = completion(request_id, body.mode, status, payload, elapsed*1000)
        return await capture(owner, body.query, event, payload.get("results", []), body.share_for_review)
    except Exception:
        # Optional telemetry must never change search availability or results.
        from app.services.search_observability import observe
        observe("vindex_search_capture_total", {"outcome": "failed"})
        return False


class LibrarySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["hybrid", "semantic", "lexical"] = "semantic"
    scope: Literal["my_library"] = "my_library"
    limit: int = Field(default=5, ge=1, le=20)
    analytics_opt_in: bool = False
    share_for_review: bool = False


@router.post("/search")
async def library_search(body: LibrarySearchRequest, user: AuthUser = Depends(get_current_user)):
    if os.getenv("LIBRARY_SEARCH_ENABLED", "false").lower() != "true":
        raise HTTPException(404, detail={"code": "library_search_disabled"})
    try:
        query = LibrarySearchQuery(**body.model_dump(exclude={"analytics_opt_in", "share_for_review"}))
    except ValueError:
        raise HTTPException(422, detail={"code": "invalid_search_query"})
    started = monotonic()
    deadline = started + 3
    request_id = str(uuid4())
    trace_token = trace_id.set(request_id.replace("-", ""))
    status, payload = 503, {}
    try:
        with span("request"):
            future = submit_search(user.user_id, query, deadline)
            wrapped = asyncio.wrap_future(future)
            wrapped.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            status, payload = await asyncio.wait_for(asyncio.shield(wrapped), timeout=max(.001, deadline - monotonic()))
        interaction_enabled = await capture_outcome(body, user.user_id, request_id, status, payload, monotonic()-started)
        return JSONResponse(dict(payload, request_id=request_id, interaction_enabled=interaction_enabled),
            status_code=status, headers={"X-Request-ID": request_id})
    except SearchError as exc:
        status = exc.status
        await capture_outcome(body, user.user_id, request_id, status, {}, monotonic()-started)
        raise HTTPException(exc.status, detail={"code": exc.code}, headers={"X-Request-ID": request_id, **({"Retry-After": "1"} if exc.status == 429 else {})})
    except Exception:
        status = 503
        await capture_outcome(body, user.user_id, request_id, status, {}, monotonic()-started)
        raise HTTPException(503, detail={"code": "library_search_unavailable"}, headers={"X-Request-ID": request_id})
    finally:
        record_response(query.mode, status, payload, monotonic() - started)
        trace_id.reset(trace_token)


@router.get("/internal/search-metrics", include_in_schema=False)
def search_metrics(authorization: str | None = Header(default=None)):
    token = os.getenv("SEARCH_METRICS_TOKEN")
    if not token:
        raise HTTPException(404)
    if not secrets.compare_digest(authorization or "", "Bearer " + token):
        raise HTTPException(401)
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")
