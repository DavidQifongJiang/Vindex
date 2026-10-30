"""Isolated phase-5 evaluation using the real search route and real stores.

Requires VINDEX_TEST_DATABASE_URL, VINDEX_TEST_QDRANT_URL and
VINDEX_TEST_ELASTICSEARCH_URL. Uses only randomly named schemas/indexes/collections.
The ASGI client measures application round trips, not ingress/TLS or JWT validation.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import random
from threading import Event
from time import monotonic, perf_counter, process_time
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema
from sentence_transformers import SentenceTransformer

from app.api.search import router
from app.db.models import Base, Video, SearchProjection
from app.db import session as database, search_repository as repo
from app.services import library_search as search, search_service, search_observability as telemetry
from app.services.auth_service import AuthUser, get_current_user
from app.services.search_contracts import TranscriptSnapshot
from app.services.projection_indexes import ElasticsearchProjectionIndex, QdrantProjectionIndex
from benchmarks.compare_retrieval import evaluate


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def summarize(rows):
    successful = [r for r in rows if r["http_status"] == 200 and not r["degraded"]]
    latencies = [r["latency_ms"] for r in rows]
    return {"requests": len(rows), "status_counts": dict(Counter(str(r["http_status"]) for r in rows)),
        "degraded": sum(r["degraded"] for r in rows),
        "p50_ms": float(np.percentile(latencies, 50)), "p95_ms": float(np.percentile(latencies, 95)),
        "quality_eligible_queries": len(successful),
        **{metric: sum(r[metric] for r in successful) / max(1, len(successful))
           for metric in ("hit_at_1", "hit_at_5", "precision_at_5", "mrr")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/library_phase5_results.json"))
    args = parser.parse_args()
    plan_path = Path("benchmarks/library_evaluation_plan.json")
    plan = json.loads(plan_path.read_text())
    corpus = json.loads(Path(plan["corpus"]).read_text(encoding="utf-8"))
    labels = list(csv.DictReader(open(plan["labels"], encoding="utf-8-sig")))
    videos = {v["video_key"]: v for v in corpus["videos"]}
    suffix = uuid4().hex
    schema, target = "vindex_eval_" + suffix, "vindex-eval-" + suffix
    root = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"])
    with root.begin() as conn:
        conn.execute(CreateSchema(schema))
    engine = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"], connect_args={"options": f"-csearch_path={schema}"})
    environment = {"LIBRARY_SEARCH_ENABLED": "true", "SEARCH_PROJECTIONS_ENABLED": "true",
        "EMBEDDING_MODEL": plan["model"], "SEARCH_EMBEDDING_REVISION": plan["model_revision"],
        "SEARCH_LEXICAL_TARGET": target, "SEARCH_SEMANTIC_TARGET": target,
        "ELASTICSEARCH_URL": os.environ["VINDEX_TEST_ELASTICSEARCH_URL"],
        "QDRANT_URL": os.environ["VINDEX_TEST_QDRANT_URL"], "SEARCH_VECTOR_SIZE": "384"}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "plan": plan,
        "hashes": {"plan": digest(plan_path), "corpus": digest(plan["corpus"]), "labels": digest(plan["labels"])},
        "videos": len(videos), "passages": sum(len(v["segments"]) for v in videos.values()),
        "measurement": "FastAPI ASGI application round trip; actual PostgreSQL/Elasticsearch/Qdrant and CPU model. Auth dependency fixed to isolated owner, no ingress/TLS/JWT; no S3 ingestion.",
        "rows": [], "scenarios": {}}
    trace_path = args.output.with_suffix(".traces.jsonl")
    handler = logging.FileHandler(trace_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    original_handlers = telemetry.logger.handlers[:]
    telemetry.logger.handlers = [handler]
    elastic = qdrant = None
    try:
        with patch.dict(os.environ, environment), patch.object(database, "engine", engine), patch.object(database, "SessionLocal", sessionmaker(engine, autoflush=False)):
            Base.metadata.create_all(engine)
            started = perf_counter()
            model = SentenceTransformer(plan["model"], revision=plan["model_revision"], local_files_only=True, device="cpu")
            if model[0].auto_model.config._commit_hash != plan["model_revision"]:
                raise ValueError("Cached model revision mismatch")
            search_service._embedding_model = model
            report["model_load_seconds"] = perf_counter() - started
            elastic = ElasticsearchProjectionIndex(target)
            qdrant = QdrantClient(url=environment["QDRANT_URL"], check_compatibility=False)
            vector_index = QdrantProjectionIndex(target, 384, qdrant)
            report["elasticsearch_version"] = elastic.request("GET", "")["version"]["number"]
            report["qdrant_version"] = qdrant.info().version
            setup_started = perf_counter()
            for key, video in videos.items():
                raw = json.dumps(video["segments"], indent=4).encode()
                with database.create_session() as db:
                    db.add(Video(video_id=video["video_id"], title=key, owner_user_id="benchmark-owner", status="processed",
                        segments_status="completed", segments_path=key + ".json"))
                    db.commit()
                    revision = repo.register_revision(db, video["video_id"], key + ".json", raw)
                    revision_id = revision.revision_id
                    db.commit()
                    jobs = [(j.job_id, j.generation_id) for j in db.query(SearchProjection).filter_by(video_id=video["video_id"]).all()]
                snapshot = TranscriptSnapshot.from_manifest(video["video_id"], revision_id, raw)
                embeddings = model.encode([p.text for p in snapshot.passages], normalize_embeddings=True, show_progress_bar=False).tolist()
                for job, generation in jobs:
                    with database.create_session() as db:
                        attempt = repo.claim(db, job)
                    index, vectors = (elastic, None) if generation == "lexical-v1" else (vector_index, embeddings)
                    index.write(snapshot, attempt, vectors)
                    if not index.verify(snapshot, attempt, vectors):
                        raise ValueError("Published benchmark data failed verification")
                    with database.create_session() as db:
                        repo.publish(db, job, attempt)
                print("Indexed", key, len(snapshot.passages), flush=True)
            report["setup_seconds"] = perf_counter() - setup_started
            report["index_store_bytes"] = elastic.request("GET", target + "/_stats/store")["_all"]["total"]["store"]["size_in_bytes"]
            report["qdrant_collection"] = qdrant.get_collection(target).model_dump(mode="json")
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_current_user] = lambda: AuthUser("benchmark-owner")
            with TestClient(app) as client:
                def request(mode, label):
                    start = perf_counter()
                    response = client.post("/search", json={"query": label["query"], "mode": mode, "limit": 5})
                    elapsed = (perf_counter() - start) * 1000
                    payload = response.json()
                    hits = payload.get("results", [])
                    if any(h["video_id"] not in {v["video_id"] for v in videos.values()} for h in hits):
                        raise AssertionError("Out-of-library result")
                    # Preserve rank positions while disqualifying wrong-video hits.
                    converted = [dict(h, start=h["start_seconds"] if h["video_id"] == videos[label["video_key"]]["video_id"] else -1e9,
                                      end=h["end_seconds"] if h["video_id"] == videos[label["video_key"]]["video_id"] else -1e9) for h in hits]
                    return {"mode": mode, "label_id": label["label_id"], "query_type": label["query_type"],
                        "http_status": response.status_code, "latency_ms": elapsed,
                        "degraded": payload.get("degraded", False), "coverage": payload.get("coverage"),
                        "branches": payload.get("branches"), "request_id": payload.get("request_id") or response.headers.get("X-Request-ID"),
                        "results": hits, **evaluate(converted, label)}
                report["first_request_after_setup"] = request("hybrid", labels[0])
                rng = random.Random(plan["seed"])
                order = list(labels)
                rng.shuffle(order)
                for label in order:
                    modes = list(plan["modes"])
                    rng.shuffle(modes)
                    for mode in modes:
                        report["rows"].append(request(mode, label))
                report["summary"] = {mode: summarize([r for r in report["rows"] if r["mode"] == mode]) for mode in plan["modes"]}
                report["by_query_type"] = {kind: {mode: summarize([r for r in report["rows"] if r["mode"] == mode and r["query_type"] == kind])
                    for mode in plan["modes"]} for kind in sorted({l["query_type"] for l in labels})}
                # Actual connection-refusal drills against unused local ports.
                for name, env in (("lexical_down", {"ELASTICSEARCH_URL": "http://127.0.0.1:1"}),
                                  ("semantic_down", {"QDRANT_URL": "http://127.0.0.1:1"}),
                                  ("both_down", {"ELASTICSEARCH_URL": "http://127.0.0.1:1", "QDRANT_URL": "http://127.0.0.1:1"})):
                    with patch.dict(os.environ, env):
                        report["scenarios"][name] = request("hybrid", labels[0])
                original = search.search_library
                def slow(backend, *args):
                    if backend == "semantic":
                        Event().wait(2.3)
                    return search.retrieve(backend, *args)
                with patch.object(search, "search_library", side_effect=lambda *a, **kw: original(*a, **kw, retriever=slow)):
                    report["scenarios"]["semantic_slow"] = request("hybrid", labels[0])
                # Allow retained work to drain before the next independent scenario.
                Event().wait(.5)
                with database.create_session() as db:
                    job = db.query(SearchProjection).filter_by(generation_id="lexical-v1").first()
                    job.status, job.accepted_attempt_id = "pending", None
                    db.commit()
                report["scenarios"]["partial_indexing"] = request("hybrid", labels[0])
                with database.create_session() as db:
                    db.query(SearchProjection).update({"status": "pending", "accepted_attempt_id": None})
                    db.commit()
                report["scenarios"]["no_ready_index"] = request("hybrid", labels[0])
                # Restore only verified publications from the accepted attempt rows.
                from app.db.models import SearchAttempt
                with database.create_session() as db:
                    for job in db.query(SearchProjection).all():
                        job.accepted_attempt_id = db.query(SearchAttempt).filter_by(job_id=job.job_id).one().attempt_id
                        job.status = "ready"
                    db.commit()
                load_started = perf_counter()
                with ThreadPoolExecutor(max_workers=4) as pool:
                    load = list(pool.map(lambda i: request("hybrid", labels[i % len(labels)]), range(32)))
                report["load"] = {"concurrency": 4, "summary": summarize(load), "requests_per_second": len(load)/(perf_counter()-load_started), "rows": load}
                report["scenarios"]["recovered"] = request("hybrid", labels[0])
                # No-answer probes are exploratory, NOT newly adjudicated labels.
                report["no_answer_probes"] = []
                for query in ("sourdough bread starter hydration", "Renaissance fresco restoration materials", "coral reef bleaching ocean acidification"):
                    probe = dict(labels[0], query=query, label_id="exploratory-no-answer")
                    for mode in plan["modes"]:
                        result = request(mode, probe)
                        report["no_answer_probes"].append({"query": query, "mode": mode,
                            "http_status": result["http_status"], "returned_count": len(result["results"]),
                            "results": result["results"]})
                # Saturate admission with injected blocked branches, then release.
                blocked, entered = Event(), Event()
                def held(*_):
                    entered.set()
                    blocked.wait(2)
                    return []
                with patch.object(search, "search_library", side_effect=lambda *a, **kw: original(*a, **kw, retriever=held)):
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        pending = [pool.submit(request, "hybrid", labels[0]) for _ in range(8)]
                        entered.wait(1)
                        until = monotonic() + 1
                        while monotonic() < until:
                            with telemetry.lock:
                                active = telemetry.counts[("vindex_library_active", ())]
                            if active == 8:
                                break
                            Event().wait(.01)
                        try:
                            report["scenarios"]["overloaded"] = request("hybrid", labels[0])
                        finally:
                            blocked.set()
                        report["admission_released_statuses"] = [f.result()["http_status"] for f in pending]
                report["scenarios"]["after_overload"] = request("hybrid", labels[0])
                # Real concurrent writes to unpublished attempt namespaces while
                # queries continue reading the accepted attempt, then delete them.
                first = next(iter(videos.values()))
                raw = json.dumps(first["segments"][:10]).encode()
                orphan = TranscriptSnapshot.from_manifest(first["video_id"], str(uuid4()), raw)
                orphan_attempt = str(uuid4())
                orphan_vectors = model.encode([p.text for p in orphan.passages], normalize_embeddings=True).tolist()
                def write_load():
                    for _ in range(8):
                        elastic.write(orphan, orphan_attempt)
                        vector_index.write(orphan, orphan_attempt, orphan_vectors)
                cpu_start, wall_start = process_time(), perf_counter()
                with ThreadPoolExecutor(max_workers=5) as pool:
                    writing = pool.submit(write_load)
                    mixed = list(pool.map(lambda i: request("hybrid", labels[i % len(labels)]), range(24)))
                    writing.result()
                report["mixed_load"] = {"concurrency": 4, "unpublished_write_cycles": 8,
                    "summary": summarize(mixed), "wall_seconds": perf_counter()-wall_start,
                    "api_process_cpu_seconds": process_time()-cpu_start,
                    "orphan_revision_returned": any(h["revision_id"] == orphan.revision_id for r in mixed for h in r["results"])}
                elastic.delete(orphan_attempt)
                vector_index.delete(orphan_attempt)
            s = report["summary"]
            gates = plan["regression_gate"]
            report["regression_gates"] = {
                "hit5": s["hybrid"]["hit_at_5"]-s["semantic"]["hit_at_5"] >= gates["hybrid_hit5_delta_min"],
                "mrr": s["hybrid"]["mrr"]-s["semantic"]["mrr"] >= gates["hybrid_mrr_delta_min"],
                "paraphrase": report["by_query_type"]["paraphrase"]["hybrid"]["hit_at_5"]-report["by_query_type"]["paraphrase"]["semantic"]["hit_at_5"] >= gates["paraphrase_hit5_delta_min"],
                "warm_p95": s["hybrid"]["p95_ms"] <= gates["warm_hybrid_p95_ms_max"],
                "healthy_errors": all(v["quality_eligible_queries"] == len(labels) for v in s.values())}
            report["rollout_recommendation"] = "hold: independent held-out relevance and deployment capacity evidence remain required"
            scenarios = report["scenarios"]
            report["failure_gates"] = {
                "lexical_fallback": scenarios["lexical_down"]["http_status"] == 200 and scenarios["lexical_down"]["degraded"],
                "semantic_fallback": scenarios["semantic_down"]["http_status"] == 200 and scenarios["semantic_down"]["degraded"],
                "both_unavailable": scenarios["both_down"]["http_status"] == 503,
                "slow_fallback": scenarios["semantic_slow"]["http_status"] == 200 and scenarios["semantic_slow"]["branches"]["semantic"]["status"] == "timeout",
                "partial_notice": scenarios["partial_indexing"]["coverage"]["complete"] is False,
                "not_ready": scenarios["no_ready_index"]["http_status"] == 409,
                "overload": scenarios["overloaded"]["http_status"] == 429,
                "recovery": scenarios["after_overload"]["http_status"] == 200 and not scenarios["after_overload"]["degraded"],
                "no_orphan_publication": not report["mixed_load"]["orphan_revision_returned"]}
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            args.output.with_suffix(".metrics.txt").write_text(telemetry.render_metrics(), encoding="utf-8", newline="\n")
            print(json.dumps({"summary": report["summary"], "gates": report["regression_gates"]}, indent=2), flush=True)
            if not all(report["failure_gates"].values()):
                raise AssertionError("Failure invariant did not pass; inspect saved report")
    finally:
        if elastic:
            try:
                elastic.request("DELETE", target)
            finally:
                elastic.close()
        if qdrant:
            if qdrant.collection_exists(target):
                qdrant.delete_collection(target)
            qdrant.close()
        engine.dispose()
        with root.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        root.dispose()
        telemetry.logger.handlers = original_handlers
        handler.close()


if __name__ == "__main__":
    main()
