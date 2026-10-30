"""Versioned content-free events and bounded, opt-in search capture."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from threading import BoundedSemaphore
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Interaction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str
    event_type: Literal["search.results_viewed", "search.result_opened", "search.feedback_submitted"]
    rank: int | None = Field(default=None, ge=1, le=20)
    ranks: list[int] | None = Field(default=None, max_length=20)
    feedback: Literal["helpful", "not_helpful"] | None = None

    @model_validator(mode="after")
    def validate_fields(self):
        UUID(self.event_id)
        if self.event_type == "search.results_viewed":
            if self.ranks is None or self.rank is not None or self.feedback is not None:
                raise ValueError("view requires ranks only")
            if len(set(self.ranks)) != len(self.ranks) or any(type(r) is not int or not 1 <= r <= 20 for r in self.ranks):
                raise ValueError("invalid ranks")
        elif self.event_type == "search.result_opened":
            if self.rank is None or self.ranks is not None or self.feedback is not None:
                raise ValueError("open requires rank only")
        elif self.feedback is None or self.ranks is not None:
            raise ValueError("feedback required")
        return self


class SearchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str
    event_type: Literal["search.completed", "search.results_viewed", "search.result_opened", "search.feedback_submitted"]
    schema_version: Literal[1] = 1
    search_id: str
    occurred_at: str
    recorded_at: str
    producer_version: str = "vindex-search-events-v1"
    traffic_class: Literal["synthetic", "opt_in"]
    requested_mode: Literal["semantic", "lexical", "hybrid"]
    effective_mode: str
    configuration: str
    status: int = Field(ge=100, le=599)
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    result_count: int = Field(ge=0, le=20)
    degraded: bool
    coverage_complete: bool
    total_videos: int = Field(ge=0)
    rank: int | None = None
    ranks: list[int] | None = None
    feedback: Literal["helpful", "not_helpful"] | None = None

    @model_validator(mode="after")
    def identities(self):
        UUID(self.event_id)
        UUID(self.search_id)
        for stamp in (self.occurred_at, self.recorded_at):
            if datetime.fromisoformat(stamp).tzinfo is None:
                raise ValueError("timezone required")
        if self.event_type != "search.completed":
            Interaction(event_id=self.event_id, event_type=self.event_type,
                        rank=self.rank, ranks=self.ranks, feedback=self.feedback)
            if any(r > self.result_count for r in (self.ranks or []) + ([self.rank] if self.rank else [])):
                raise ValueError("rank outside results")
        elif self.rank is not None or self.ranks is not None or self.feedback is not None:
            raise ValueError("completion cannot contain interaction")
        return self


def enabled(name):
    return os.getenv(name, "false").lower() == "true"


def telemetry_allowed():
    return enabled("AUTH_REQUIRED") or enabled("SEARCH_EVENTS_SYNTHETIC_ONLY")


def completion(search_id, mode, status, payload, elapsed_ms):
    stamp = datetime.now(timezone.utc).isoformat()
    coverage = payload.get("coverage", {})
    return SearchEvent(event_id=str(uuid4()), event_type="search.completed", search_id=search_id,
        occurred_at=stamp, recorded_at=stamp,
        traffic_class="synthetic" if enabled("SEARCH_EVENTS_SYNTHETIC_ONLY") else "opt_in",
        requested_mode=mode, effective_mode=payload.get("effective_mode", "none"),
        configuration=payload.get("configuration_version", os.getenv("SEARCH_EVENTS_CONFIGURATION", "unknown-before-retrieval")), status=status,
        elapsed_ms=float(elapsed_ms), result_count=len(payload.get("results", [])),
        degraded=bool(payload.get("degraded", False)), coverage_complete=bool(coverage.get("complete", False)),
        total_videos=coverage.get("total_videos", 0)).model_dump()


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-capture")
_slots = BoundedSemaphore(2)


async def capture(owner, query, event, results, share):
    from app.db.search_event_repository import capture_search
    from app.services.search_observability import observe
    if not _slots.acquire(blocking=False):
        observe("vindex_search_capture_total", {"outcome": "busy"})
        return False
    try:
        future = _executor.submit(capture_search, owner, query, event, results, share)
    except Exception:
        _slots.release()
        return False
    def completed(done):
        _slots.release()
        observe("vindex_search_capture_total", {"outcome": "committed" if done.exception() is None else "failed"})
    future.add_done_callback(completed)
    try:
        await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout=.025)
        return True
    except asyncio.TimeoutError:
        # The bounded worker can still commit. Never call this definite event loss.
        observe("vindex_search_capture_total", {"outcome": "timeout_unknown"})
        return False
    except Exception:
        return False
