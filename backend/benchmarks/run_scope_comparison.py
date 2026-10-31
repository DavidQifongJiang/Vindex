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
    parser.add_argument("--output", type=Path, default=Path("tmp/search-scope-comparison-01/results.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=False)
    plan_path = Path("benchmarks/library_evaluation_plan.json")
    plan = json.loads(plan_path.read_text())
    corpus = json.loads(Path(plan["corpus"]).read_text(encoding="utf-8"))
    labels = list(csv.DictReader(open(plan["labels"], encoding="utf-8-sig")))
    if len(labels) != 100 or len(corpus["videos"]) != 10:
        raise ValueError("Expected original100 queries and10 videos")
    args.output.with_name("plan.json").write_text(json.dumps(dict(source_plan=plan, rounds=2, scopes=["within_video", "library"], seed=20260922, scoring="Original designated-video timestamp rule; alternative passages remain unjudged", corpus_sha256=digest(plan["corpus"]), labels_sha256=digest(plan["labels"])), indent=2))
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
                original_search = search.search_library
                def request(mode, label, scope, repeat):
                    video_id = videos[label['video_key']]['video_id']
                    def scope_reader(owner, deadline):
                        current = search.read_scope(owner, deadline)
                        if scope == 'library':
                            return current
                        return search.Scope(
                            {k:v for k,v in current.videos.items() if k == video_id},
                            {b:{k:v for k,v in items.items() if k == video_id} for b,items in current.projections.items()},
                            current.generations, {p for p in current.failed if p[1] == video_id})
                    def scoped_search(*a, **kw):
                        return original_search(*a, **kw, scope_reader=scope_reader)

                    start = perf_counter()
                    with patch.object(search, "search_library", scoped_search):
                        response = client.post("/search", json={"query": label["query"], "mode": mode, "limit": 5})
                    elapsed = (perf_counter() - start) * 1000
                    payload = response.json()
                    hits = payload.get("results", [])
                    if any(h["video_id"] not in {v["video_id"] for v in videos.values()} for h in hits):
                        raise AssertionError("Out-of-library result")
                    # Preserve rank positions while disqualifying wrong-video hits.
                    converted = [dict(h, start=h["start_seconds"] if h["video_id"] == videos[label["video_key"]]["video_id"] else -1e9,
                                      end=h["end_seconds"] if h["video_id"] == videos[label["video_key"]]["video_id"] else -1e9) for h in hits]
                    if scope == "within_video" and any(h["video_id"] != video_id for h in hits):
                        raise AssertionError("Wrong-video scoped result")
                    return {"scope": scope, "round": repeat, "query": label["query"], "mode": mode, "label_id": label["label_id"], "query_type": label["query_type"],
                        "http_status": response.status_code, "latency_ms": elapsed,
                        "degraded": payload.get("degraded", False), "coverage": payload.get("coverage"),
                        "branches": payload.get("branches"), "request_id": payload.get("request_id") or response.headers.get("X-Request-ID"),
                        "results": hits, **evaluate(converted, label)}
                report['warmups'] = [request(m, labels[0], scope, -1) for m in plan['modes'] for scope in ['within_video','library']]
                rng = random.Random(20260922)
                for repeat in range(2):
                    order = list(labels)
                    rng.shuffle(order)
                    for i,label in enumerate(order,1):
                        conditions = [(m,scope) for m in plan['modes'] for scope in ['within_video','library']]
                        rng.shuffle(conditions)
                        for mode,scope in conditions:
                            report['rows'].append(request(mode,label,scope,repeat))
                        if i % 10 == 0:
                            args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
                            print(f'Round {repeat+1}: {i}/100 queries',flush=True)
                report['summary'] = {scope:{m:summarize([r for r in report['rows'] if r['scope']==scope and r['mode']==m]) for m in plan['modes']} for scope in ['within_video','library']}
                report['complete'] = True
                args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    finally:
        from app.services.search_clients import close_search_clients
        close_search_clients()
        cleanup = {}
        for name,operation in [('elasticsearch',lambda:elastic.request('DELETE',target) if elastic else None),('qdrant',lambda:qdrant.delete_collection(target) if qdrant else None),('postgres',lambda:drop_schema(root,schema))]:
            try:
                operation();cleanup[name]='done'
            except Exception as exc:
                cleanup[name]=type(exc).__name__
        if elastic:elastic.close()
        if qdrant:qdrant.close()
        engine.dispose();root.dispose()
        telemetry.logger.handlers=original_handlers;handler.close()
        report['cleanup']=cleanup
        args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    from benchmarks.summarize_scope_comparison import generate
    generate(args.output)


def drop_schema(engine,schema):
    with engine.begin() as conn:
        conn.execute(DropSchema(schema,cascade=True))


if __name__ == '__main__':
    main()
