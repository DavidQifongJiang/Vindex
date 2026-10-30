"""Bounded library retrieval; only Postgres authorizes searchable projections."""
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
from copy import deepcopy
import math
import os
import requests
import httpx
from threading import BoundedSemaphore
from time import monotonic

from sqlalchemy import text
from qdrant_client import models as qm
from qdrant_client.http.exceptions import ResponseHandlingException

from app.db.models import Video, SearchRevision, SearchProjection, SearchConfiguration, SearchGeneration, SearchDeletion
from app.db.session import create_session
from app.services.search_contracts import Passage
from app.services.search_policy import REQUESTED, search_outcome
from app.services.hybrid_search import reciprocal_rank_fusion
from app.services.projection_indexes import ElasticsearchProjectionIndex
from app.services.lexical_query import lexical_query
from app.services.search_clients import semantic_client
from app.services.search_observability import span, observe, event

ADMISSION = BoundedSemaphore(8)
EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="library-request")


class SearchError(Exception):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def remaining(deadline):
    value = deadline - monotonic()
    if value <= 0:
        raise TimeoutError("Search deadline exhausted")
    return value


@dataclass
class Scope:
    videos: dict
    projections: dict
    generations: dict
    failed: set


def read_scope(owner, deadline, sessions=create_session):
    with sessions() as db:
        # A fresh transaction on each read prevents an identity-map or snapshot
        # from hiding deletion, ownership transfer, revision change, or repair.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            db.execute(text("SELECT set_config('statement_timeout', :budget, true)"),
                       {"budget": str(max(1, int(remaining(deadline) * 1000)))})
        rows = db.query(Video).filter(Video.owner_user_id == owner, Video.status != "deleting",
            ~Video.video_id.in_(db.query(SearchDeletion.video_id))).order_by(Video.video_id).limit(101).all()
        if len(rows) > 100:
            raise SearchError("library_scope_too_large", 422)
        videos = {v.video_id: {"title": v.title, "revision": v.current_search_revision_id,
                              "path": v.segments_path} for v in rows}
        config = db.get(SearchConfiguration, "active")
        active = dict(config.generations) if config else {}
        generations, projections, failed = {}, {b: {} for b in REQUESTED["hybrid"]}, set()
        for backend, generation_id in active.items():
            if backend not in projections:
                raise ValueError("Unknown configured backend")
            gen = db.get(SearchGeneration, generation_id)
            if gen is None or gen.backend != backend:
                raise ValueError("Invalid active generation")
            generations[backend] = {"id": generation_id, "target": gen.target, "settings": dict(gen.settings)}
            if not videos:
                continue
            jobs = db.query(SearchProjection, SearchRevision).join(SearchRevision,
                SearchProjection.revision_id == SearchRevision.revision_id).filter(
                SearchProjection.video_id.in_(videos), SearchProjection.generation_id == generation_id).all()
            for job, revision in jobs:
                video = videos[job.video_id]
                if revision.video_id != job.video_id or (job.revision_id, revision.segments_path) != (video["revision"], video["path"]):
                    continue
                if job.status == "ready" and job.accepted_attempt_id:
                    projections[backend][job.video_id] = (job.revision_id, job.accepted_attempt_id)
                elif job.status == "failed":
                    failed.add((backend, job.video_id))
        remaining(deadline)
        return Scope(videos, projections, generations, failed)


def lexical_filter(projections):
    if not projections:
        raise ValueError("Unscoped search prohibited")
    return {"bool": {"should": [{"bool": {"filter": [
        {"term": {"video_id": video}}, {"term": {"revision_id": revision}},
        {"term": {"projection_id": attempt}}]}} for video, (revision, attempt) in projections.items()],
        "minimum_should_match": 1}}


def semantic_filter(projections):
    if not projections:
        raise ValueError("Unscoped search prohibited")
    return qm.Filter(should=[qm.Filter(must=[qm.FieldCondition(key=key, match=qm.MatchValue(value=value))
        for key, value in (("video_id", video), ("revision_id", revision), ("projection_id", attempt))])
        for video, (revision, attempt) in projections.items()])


def retrieve(backend, generation, projections, query, deadline, limit=50, *, lexical_profile="baseline"):
    if not projections:
        raise ValueError("Unscoped search prohibited")
    if backend == "lexical":
        index = ElasticsearchProjectionIndex(generation["target"])
        try:
            budget = remaining(deadline)
            result = index.request("POST", index.target + "/_search", timeout=budget,
                params={"allow_partial_search_results": "false"}, json={"size": limit,
                "timeout": f"{max(1, int(budget * 1000))}ms",
                "query": {"bool": {"filter": [lexical_filter(projections)], "must": [lexical_query(query, lexical_profile)]}},
                "sort": [{"_score": "desc"}, {"passage_id": "asc"}]})
            if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
                raise TimeoutError("Incomplete lexical result")
            remaining(deadline)
            return [dict(h["_source"], score=h["_score"]) for h in result["hits"]["hits"]]
        finally:
            index.close()
    from app.services.search_service import get_embedding_model
    settings = generation["settings"]
    with span("embedding_model"):
        model = get_embedding_model()
    if (os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2") != settings["model"] or
            getattr(model[0].auto_model.config, "_commit_hash", None) != settings["model_revision"]):
        raise ValueError("Query model does not match published generation")
    with span("embedding"):
        vector = model.encode(query).tolist()
    if len(vector) != settings["dimension"] or not all(math.isfinite(x) for x in vector) or not any(vector):
        raise ValueError("Invalid query embedding")
    with semantic_client() as client:
        # SDK REST timeouts have whole-second resolution. Recompute after client
        # acquisition; never mutate shared transport timeout configuration.
        budget = remaining(deadline)
        with span("qdrant"):
            response = client.query_points(generation["target"], query=vector,
                query_filter=semantic_filter(projections), limit=limit, with_payload=True, with_vectors=False,
                timeout=max(1, math.ceil(budget)))
        remaining(deadline)
        return [dict(p.payload, score=p.score) for p in response.points]


def validate_hits(hits, projections):
    clean = []
    for hit in hits:
        passage = Passage.from_hit(hit)
        if projections.get(passage.video_id) != (passage.revision_id, hit.get("projection_id")):
            continue
        if not math.isfinite(float(hit["score"])):
            raise ValueError("Invalid backend score")
        clean.append(dict(passage.as_hit(), score=hit["score"]))
    return sorted(clean, key=lambda h: (-h["score"], h["passage_id"]))


def search_library(owner, query, deadline, *, scope_reader=read_scope, retriever=retrieve, deliver=None,
                   candidate_observer=None):
    """An internal observer receives only revalidated candidates, never public output.

    Intended for isolated evaluation, not logging. Observers must be inexpensive;
    their work shares the request deadline. Copies prevent accidental mutation of
    serving results. No API request can enable this hook or select query profiles.
    """
    with span("scope"):
        scope = scope_reader(owner, min(deadline, monotonic() + .5))
    branch_deadline = min(deadline - .25, monotonic() + 2)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="library-branch")
    futures, statuses, rankings = {}, {}, {}
    try:
        for backend in REQUESTED[query.mode]:
            if scope.projections[backend]:
                def branch(name=backend):
                    with span(name):
                        return retriever(name, scope.generations[name], scope.projections[name], query.query, branch_deadline)
                futures[backend] = executor.submit(copy_context().run, branch)
        wait(list(futures.values()), timeout=max(0, branch_deadline - monotonic()))
        for backend, future in futures.items():
            if not future.done():
                statuses[backend] = "timeout"
                continue
            try:
                rankings[backend] = validate_hits(future.result(), scope.projections[backend])[:50]
                statuses[backend] = "ok"
            except (TimeoutError, requests.Timeout, httpx.TimeoutException):
                statuses[backend] = "timeout"
            except ResponseHandlingException as exc:
                statuses[backend] = "timeout" if isinstance(exc.source, httpx.TimeoutException) else "failed"
            except Exception:
                event("search_branch_failed", backend=backend)
                statuses[backend] = "failed"
        with span("revalidation"):
            current = scope_reader(owner, deadline)
        # Only projections used for this request may contribute. Newly published
        # work belongs to the next request, avoiding false coverage claims.
        eligible = {b: {v: p for v, p in scope.projections[b].items()
            if current.projections[b].get(v) == p and current.generations.get(b) == scope.generations.get(b)}
            for b in scope.projections}
        for backend in list(statuses):
            rankings[backend] = [h for h in rankings.get(backend, []) if h["video_id"] in eligible[backend]]
            if not eligible[backend]:
                statuses.pop(backend)
        failed = {v for b, v in current.failed if b in REQUESTED[query.mode] and v not in eligible[b]}
        ready = set(eligible["semantic"]) | set(eligible["lexical"])
        processing = set(current.videos) - ready - failed
        outcome = search_outcome(query.mode, video_ids=current.videos,
            semantic_ids=eligible["semantic"], lexical_ids=eligible["lexical"], statuses=statuses,
            processing_ids=processing, indexing_failed_ids=failed)
        with span("fusion"):
            hits = reciprocal_rank_fusion(*(rankings.get(b, []) for b in REQUESTED[query.mode]), limit=query.limit)
        matched = {b: {h["passage_id"] for h in ranking} for b, ranking in rankings.items()}
        results = [dict({key: value for key, value in h.items() if key not in {"start", "end", "score"}},
            start_seconds=h["start"], end_seconds=h["end"], title=current.videos[h["video_id"]]["title"],
            matched_by=[b for b in REQUESTED[query.mode] if h["passage_id"] in matched.get(b, set())]) for h in hits]
        remaining(deadline)
        from hashlib import sha256
        import json
        configuration_version = sha256(json.dumps({"generations": scope.generations,
            "ranking": "rrf-k60-depth50-v1"}, sort_keys=True).encode()).hexdigest()
        payload = dict(outcome.as_dict(), results=results, configuration_version=configuration_version)
        if current.videos.keys() != scope.videos.keys() or eligible != scope.projections:
            payload["notices"].append("search_scope_changed")
        if candidate_observer is not None:
            # Invoke only after fusion has checked canonical identity consistency
            # and scope has been freshly read. Never expose raw engine payloads.
            candidate_observer(deepcopy({"rankings": rankings, "branches": payload["branches"],
                                         "coverage": payload["coverage"], "http_status": outcome.http_status}))
            remaining(deadline)
        response = outcome.http_status, payload
        if deliver:
            deliver(response)
        return response
    finally:
        # Admission remains occupied until even timed-out blocking calls finish.
        # The HTTP caller has its own deadline and need not wait for this cleanup.
        executor.shutdown(wait=True, cancel_futures=True)


def submit_search(owner, query, deadline):
    if not ADMISSION.acquire(blocking=False):
        raise SearchError("library_search_overloaded", 429)
    observe("vindex_library_active", {}, amount=1)
    try:
        response = Future()
        def execute():
            try:
                search_library(owner, query, deadline, deliver=response.set_result)
            except BaseException as exc:
                if not response.done():
                    response.set_exception(exc)
            finally:
                observe("vindex_library_active", {}, amount=-1)
                ADMISSION.release()
        EXECUTOR.submit(copy_context().run, execute)
        return response
    except BaseException:
        observe("vindex_library_active", {}, amount=-1)
        ADMISSION.release()
        raise
