"""Bounded-cardinality metrics and JSON request traces, without query content."""
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
from threading import Lock
from time import monotonic, time
from uuid import uuid4

trace_id = ContextVar("search_trace_id", default=None)
parent_id = ContextVar("search_parent_span", default=None)
logger = logging.getLogger("vindex.search.events")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False
lock = Lock()
counts = defaultdict(float)
BUCKETS = (.01, .025, .05, .1, .25, .5, 1., 2., 3., 5.)


def event(name, **fields):
    logger.info(json.dumps({"event": name, "timestamp": time(), "trace_id": trace_id.get(), **fields}, allow_nan=False))


def observe(name, labels, seconds=None, amount=1):
    """Call sites use fixed names/labels only: no user, video, query or request IDs."""
    labels = tuple(sorted(labels.items()))
    with lock:
        if seconds is None:
            counts[(name, labels)] += amount
        else:
            counts[(name + "_count", labels)] += 1
            counts[(name + "_sum", labels)] += seconds
            for bound in (*BUCKETS, "+Inf"):
                counts[(name + "_bucket", tuple(sorted((*labels, ("le", str(bound))))))] += int(bound == "+Inf" or seconds <= bound)


@contextmanager
def span(name):
    identity, parent, started = uuid4().hex[:16], parent_id.get(), monotonic()
    token = parent_id.set(identity)
    status = "ok"
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        duration = monotonic() - started
        parent_id.reset(token)
        observe("vindex_library_stage_seconds", {"stage": name, "status": status}, duration)
        event("search_span", span=name, span_id=identity, parent_span_id=parent,
              duration_seconds=duration, status=status)


def record_response(mode, status, payload, elapsed):
    outcome = "degraded" if payload.get("degraded") and status == 200 else "ok" if status == 200 else "not_ready" if status == 409 else "rejected" if status in (422, 429) else "unavailable"
    labels = {"mode": mode, "outcome": outcome}
    observe("vindex_library_requests_total", labels)
    observe("vindex_library_request_seconds", labels, elapsed)
    if payload.get("coverage", {}).get("complete") is False:
        observe("vindex_library_incomplete_total", {"mode": mode})
    for backend, state in payload.get("branches", {}).items():
        observe("vindex_library_branch_total", {"backend": backend, "status": state["status"]})
    event("search_response", mode=mode, status=status, outcome=outcome,
          elapsed_seconds=elapsed, coverage_complete=payload.get("coverage", {}).get("complete"))


def render_metrics():
    with lock:
        snapshot = dict(counts)
    definitions = {
        "vindex_library_request_seconds": ("histogram", "Library request duration in seconds."),
        "vindex_library_stage_seconds": ("histogram", "Library stage duration in seconds."),
        "vindex_library_active": ("gauge", "Admitted work including timed-out background work."),
        "vindex_library_requests_total": ("counter", "Library requests by mode and outcome."),
        "vindex_library_branch_total": ("counter", "Requested and unrequested branch states."),
        "vindex_library_incomplete_total": ("counter", "Requests with incomplete library coverage.")}
    lines = [line for name, (kind, description) in definitions.items()
             for line in (f"# HELP {name} {description}", f"# TYPE {name} {kind}")]
    for (name, labels), value in sorted(snapshot.items()):
        suffix = "{" + ",".join(f'{key}="{value}"' for key, value in labels) + "}" if labels else ""
        lines.append(f"{name}{suffix} {value}")
    return "\n".join(lines) + "\n"
