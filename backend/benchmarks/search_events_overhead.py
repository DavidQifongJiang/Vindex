"""Paired capture-on/off API timing: real 50-video search stores, four clients."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import csv
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import random
import subprocess
import time
from unittest.mock import patch
from uuid import uuid4, uuid5, NAMESPACE_URL

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
from app.db.search_event_models import SearchEventBudget
from app.db import session as database, search_repository as search_repo, search_event_repository as events
from app.services import search_service, search_observability
from app.services.auth_service import AuthUser, get_current_user
from app.services.search_contracts import TranscriptSnapshot
from app.services.projection_indexes import ElasticsearchProjectionIndex, QdrantProjectionIndex
from benchmarks.smoke_library_sample import passages, REVISION


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--broker-outage", action="store_true", help="After timings, test real searches during a60s isolated broker outage")
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    rootpath = Path(__file__).resolve().parent
    queries = list(csv.DictReader((rootpath/"library_queries_500/queries.csv").open(encoding="utf-8")))
    source = json.loads((rootpath/"lecture_captions/manifest.json").read_text())["captions"]
    schema, target = "event_overhead_"+uuid4().hex[:12], "event-overhead-"+uuid4().hex[:12]
    root = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"])
    with root.begin() as db:
        db.execute(CreateSchema(schema))
    engine = create_engine(os.environ["VINDEX_TEST_DATABASE_URL"], connect_args={"options": f"-csearch_path={schema}"})
    env = {"LIBRARY_SEARCH_ENABLED": "true", "SEARCH_PROJECTIONS_ENABLED": "true",
        "SEARCH_EVENTS_DATABASE_URL": os.environ["VINDEX_TEST_DATABASE_URL"], "SEARCH_EVENTS_SCHEMA": schema,
        "SEARCH_EVENTS_CAPTURE_ENABLED": "true", "SEARCH_EVENTS_SYNTHETIC_ONLY": "true",
        "EMBEDDING_MODEL": "all-MiniLM-L6-v2", "SEARCH_EMBEDDING_REVISION": REVISION,
        "SEARCH_LEXICAL_TARGET": target, "SEARCH_SEMANTIC_TARGET": target, "SEARCH_VECTOR_SIZE": "384",
        "ELASTICSEARCH_URL": os.environ["VINDEX_TEST_ELASTICSEARCH_URL"], "QDRANT_URL": os.environ["VINDEX_TEST_QDRANT_URL"]}
    report = dict(videos=50, clients=4, rounds=3, samples_per_condition_per_round=1000, rows=[],
        limits="Local ASGI; real stores and embedding; fixed test auth; no external network/TLS/JWT. Run without concurrent load benchmarks; record any other host contention.",
        schema=schema, target=target, corpus_manifest_sha256=sha256((rootpath/"lecture_captions/manifest.json").read_bytes()).hexdigest())
    elastic = qdrant = None
    search_observability.logger.disabled = True
    try:
        with patch.dict(os.environ, env), patch.object(database, "engine", engine), patch.object(database, "SessionLocal", sessionmaker(engine, autoflush=False)):
            Base.metadata.create_all(engine)
            events.sessions.cache_clear()
            with database.create_session() as db:
                db.add(SearchEventBudget(name="outbox", pending_bytes=0, expired_events=0)); db.commit()
            model = SentenceTransformer("all-MiniLM-L6-v2", revision=REVISION, local_files_only=True, device="cpu")
            search_service._embedding_model = model
            elastic = ElasticsearchProjectionIndex(target)
            qdrant = QdrantClient(url=env["QDRANT_URL"], check_compatibility=False)
            vector_index = QdrantProjectionIndex(target, 384, qdrant)
            for index, entry in enumerate(source):
                key, path = entry["video_key"], rootpath.parent/entry["file_path"]
                if sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ValueError("corpus changed")
                raw = json.dumps(passages(path)).encode()
                video_id = str(uuid5(NAMESPACE_URL, key))
                with database.create_session() as db:
                    db.add(Video(video_id=video_id, title=key, owner_user_id="overhead-owner", status="processed", segments_status="completed", segments_path=key+".json")); db.commit()
                    revision = search_repo.register_revision(db, video_id, key+".json", raw); revision_id = revision.revision_id; db.commit()
                    jobs = [(j.job_id, j.generation_id) for j in db.query(SearchProjection).filter_by(video_id=video_id)]
                snapshot = TranscriptSnapshot.from_manifest(video_id, revision_id, raw)
                vectors = model.encode([p.text for p in snapshot.passages], normalize_embeddings=True, batch_size=32).tolist()
                for job, generation in jobs:
                    with database.create_session() as db: attempt = search_repo.claim(db, job)
                    store, embeddings = (elastic, None) if generation == "lexical-v1" else (vector_index, vectors)
                    store.write(snapshot, attempt, embeddings)
                    if not store.verify(snapshot, attempt, embeddings): raise ValueError("index verification")
                    with database.create_session() as db: search_repo.publish(db, job, attempt)
                if index % 10 == 9: print("Indexed", index+1, flush=True)
            app = FastAPI(); app.include_router(router)
            app.dependency_overrides[get_current_user] = lambda: AuthUser("overhead-owner")
            with TestClient(app) as client:
                def request(item):
                    query_id, mode, capture, round_id = item
                    start = time.perf_counter()
                    response = client.post("/search", json={"query": queries[query_id]["query"], "mode": mode,
                        "limit": 5, "analytics_opt_in": capture})
                    elapsed = (time.perf_counter()-start)*1000
                    data = response.json()
                    return dict(query_id=query_id, mode=mode, capture=capture, round=round_id,
                        latency_ms=elapsed, status=response.status_code, capability=data.get("interaction_enabled", False),
                        hits=[h["passage_id"] for h in data.get("results", [])])
                for mode in ("semantic", "lexical"):
                    for i in range(10): request((i, mode, True, -1))
                for round_id in range(3):
                    # Both conditions contain identical 500 queries x two modes.
                    items = [(q, m, c, round_id) for q in range(500) for m in ("semantic", "lexical") for c in (False, True)]
                    random.Random(20260918+round_id).shuffle(items)
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        report["rows"].extend(pool.map(request, items))
                    print("Completed overhead round", round_id+1, flush=True)
                    (output/"results.json").write_text(json.dumps(report), encoding="utf-8")
                if args.broker_outage:
                    stopped = False
                    try:
                        subprocess.run(["docker", "stop", "vindex-analytics-kafka-1"], check=True, capture_output=True, timeout=30)
                        stopped = True
                        outage_started = time.monotonic()
                        outage_rows = []
                        with ThreadPoolExecutor(max_workers=4) as pool:
                            while time.monotonic()-outage_started < 60:
                                base = len(outage_rows)
                                outage_rows.extend(pool.map(request, [((base+i)//2 % 500,
                                    "semantic" if i % 2 else "lexical", True, -2) for i in range(4)]))
                        expected_hits = {(r["query_id"], r["mode"]): r["hits"] for r in report["rows"] if not r["capture"]}
                        report["broker_outage_search"] = dict(duration_seconds=time.monotonic()-outage_started,
                            requests=len(outage_rows), errors=sum(r["status"] != 200 for r in outage_rows),
                            changed_rankings=sum(r["hits"] != expected_hits[r["query_id"], r["mode"]] for r in outage_rows),
                            capabilities=sum(r["capability"] for r in outage_rows),
                            scope="real50-lecture stores, four ASGI clients, capture enabled; publisher recovery is a separate drill")
                        (output/"outage_requests.json").write_text(json.dumps(outage_rows), encoding="utf-8")
                    finally:
                        if stopped:
                            subprocess.run(["docker", "start", "vindex-analytics-kafka-1"], check=True, capture_output=True, timeout=30)
            report["summary"] = []
            for mode in ("semantic", "lexical"):
                row = {"mode": mode}
                for enabled in (False, True):
                    samples = [r for r in report["rows"] if r["capture"] == enabled and r["mode"] == mode]
                    row["on" if enabled else "off"] = dict(p50_ms=float(np.percentile([r["latency_ms"] for r in samples], 50)),
                        p95_ms=float(np.percentile([r["latency_ms"] for r in samples], 95)), errors=sum(r["status"] != 200 for r in samples),
                        capabilities=sum(r["capability"] for r in samples), requests=len(samples))
                increase = row["on"]["p95_ms"]-row["off"]["p95_ms"]
                row["p95_increase_ms"] = increase
                row["overhead_pass"] = increase <= 20 and increase <= row["off"]["p95_ms"]*.1
                report["summary"].append(row)
            pairs = {}
            for row in report["rows"]:
                pairs.setdefault((row["query_id"], row["mode"], row["round"]), {})[row["capture"]] = row["hits"]
            report["changed_rankings"] = sum(p[False] != p[True] for p in pairs.values())
            # Preserve each preregistered round: pooling can hide a failed run.
            report["round_summary"] = []
            for round_id in range(3):
                for mode in ("all", "semantic", "lexical"):
                    p95 = {}
                    for capture in (False, True):
                        samples = [r["latency_ms"] for r in report["rows"]
                            if r["round"] == round_id and r["capture"] == capture
                            and (mode == "all" or r["mode"] == mode)]
                        p95[capture] = float(np.percentile(samples, 95))
                    increase = p95[True] - p95[False]
                    report["round_summary"].append(dict(round=round_id + 1, mode=mode,
                        off_p95_ms=p95[False], on_p95_ms=p95[True], increase_ms=increase,
                        overhead_pass=increase <= 20 and increase <= p95[False] * .1))
            report["three_round_total_overhead_pass"] = all(
                r["overhead_pass"] for r in report["round_summary"] if r["mode"] == "all"
            ) and all(r["status"] == 200 for r in report["rows"])
            # Await bounded late capture completions before measuring durable
            # acceptance. Warmups also create contexts, so count them separately.
            from app.services.search_events import _slots
            acquired = 0
            try:
                for _ in range(2):
                    if not _slots.acquire(timeout=5):
                        raise TimeoutError("late capture did not finish")
                    acquired += 1
            finally:
                for _ in range(acquired):
                    _slots.release()
            from app.db.search_event_models import SearchEventContext
            from sqlalchemy import select, func
            with events.sessions()() as db:
                report["durable_contexts_including_warmups_and_optional_outage"] = db.scalar(select(func.count()).select_from(SearchEventContext))
    finally:
        if elastic:
            elastic.request("DELETE", target); elastic.close()
        if qdrant:
            qdrant.delete_collection(target); qdrant.close()
        engine.dispose()
        (output/"results.json").write_text(json.dumps(report), encoding="utf-8")
    print(json.dumps(report.get("summary", {}), indent=2))


if __name__ == "__main__":
    main()
