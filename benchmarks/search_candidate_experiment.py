"""Capture authorized development candidates; no automatic relevance judgments.

Run against docker-compose.quality.yml. Every run uses new schema/collections and
an exclusive output directory. Captions are development evidence, not ASR release
evidence. No production environment file is loaded.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import platform
import random
import subprocess
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4, uuid5, NAMESPACE_URL

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api.search import router
from app.db import session as database, search_repository as repo
from app.db.models import Base, Video, SearchProjection
from app.services import library_search, search_service, search_observability
from app.services.auth_service import AuthUser, get_current_user
from app.services.lexical_query import PROFILES
from app.services.projection_indexes import ElasticsearchProjectionIndex, QdrantProjectionIndex
from app.services.search_contracts import TranscriptSnapshot
from benchmarks.smoke_library_sample import passages, REVISION

ROOT = Path(__file__).resolve().parent.parent
SEED = 20260917
LOCAL_DATABASE = "postgresql+psycopg2://vindex_quality:local_quality_only@127.0.0.1:15433/vindex_quality"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def load_queries(limit):
    path = ROOT / "benchmarks/library_queries_500/development_150.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 150 or len({q["query_id"] for q in rows}) != 150 or any(q["split"] != "development" for q in rows):
        raise ValueError("Expected the unchanged development split")
    # Interleave categories for small smoke runs; full runs still use every query.
    groups = {kind: [q for q in rows if q["query_type"] == kind] for kind in sorted({q["query_type"] for q in rows})}
    ordered = [group[i] for i in range(max(map(len, groups.values()))) for group in groups.values() if i < len(group)]
    return ordered[:limit], sha256(path.read_bytes()).hexdigest()


def summarize(report):
    result = {}
    for profile in report["plan"]["profiles"]:
        rows = [r for r in report["rows"] if r["profile"] == profile]
        times = [r["latency_ms"] for r in rows]
        result[profile] = {"requests": len(rows),
            "http_statuses": dict(Counter(str(r["http_status"]) for r in rows)),
            "degraded": sum(r["degraded"] for r in rows),
            "complete": sum(r["coverage"].get("complete") is True for r in rows),
            "p50_ms": float(np.percentile(times, 50)) if times else None,
            "p95_ms": float(np.percentile(times, 95)) if times else None}
    expected = {(q["query_id"], p, n) for q in report["plan"]["queries"]
                for p in PROFILES for n in range(report["plan"]["rounds"])}
    actual = [(r["query_id"], r["profile"], r["round"]) for r in report["rows"]]
    return {"profiles": result, "integrity_complete": len(actual) == len(set(actual)) and set(actual) == expected,
            "quality_gate": "BLOCKED: candidate judgments and fresh confirmation queries required",
            "deployment_gate": "BLOCKED: local fixed-owner ASGI, captions, sequential load"}


def run(args):
    queries, query_hash = load_queries(args.query_limit)
    args.output.mkdir(parents=True, exist_ok=False)
    sources = json.loads((ROOT / "benchmarks/lecture_captions/manifest.json").read_text())["captions"]
    schema, target = "vindex_quality_" + uuid4().hex, "vindex-quality-" + uuid4().hex
    images = {}
    for service in ("postgres", "elasticsearch", "qdrant"):
        # Record actual running images, not mutable tag names, before experiments.
        images[service] = subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}",
            "vindex-quality-" + service + "-1"], text=True).strip()
    plan = {"created_at": datetime.now(timezone.utc).isoformat(), "seed": SEED,
        "profiles": list(PROFILES), "rounds": args.rounds, "queries": queries,
        "query_source_sha256": query_hash, "model": "all-MiniLM-L6-v2", "model_revision": REVISION,
        "candidate_depth": 50, "rrf_k": 60, "image_ids": images,
        "caption_hashes": {s["video_key"]: s["sha256"] for s in sources},
        "code_hashes": {p: sha256((ROOT / p).read_bytes()).hexdigest() for p in (
            "app/services/library_search.py", "app/services/lexical_query.py", "app/services/hybrid_search.py",
            "benchmarks/search_candidate_experiment.py", "benchmarks/smoke_library_sample.py")},
        "platform": platform.platform(), "python": platform.python_version(),
        "cpu_count": os.cpu_count(), "torch_threads": args.threads,
        "measurement": "Warm fixed-owner ASGI /search; real scope/revalidation and stores; hybrid requests for all profiles. No JWT/TLS/ingress, ASR, ingestion or concurrent load. Candidate capture is included in latency.",
        "selection": "Development only; no tuning or automatic winner selection in this runner"}
    write_json(args.output / "plan.json", plan)
    report = {"plan": plan, "rows": [], "temporary_schema": schema, "temporary_target": target,
              "passages": 0, "cleanup": {}, "completed": False}
    root = create_engine(LOCAL_DATABASE)
    engine = create_engine(LOCAL_DATABASE, connect_args={"options": "-csearch_path=" + schema})
    elastic = qdrant = None
    created_schema = False
    old_model = search_service._embedding_model
    old_level = search_observability.logger.level
    search_observability.logger.setLevel(logging.ERROR)
    environment = {"LIBRARY_SEARCH_ENABLED": "true", "SEARCH_PROJECTIONS_ENABLED": "true",
        "EMBEDDING_MODEL": plan["model"], "SEARCH_EMBEDDING_REVISION": REVISION,
        "SEARCH_LEXICAL_TARGET": target, "SEARCH_SEMANTIC_TARGET": target, "SEARCH_VECTOR_SIZE": "384",
        "ELASTICSEARCH_URL": "http://127.0.0.1:19201", "QDRANT_URL": "http://127.0.0.1:16336"}
    try:
        with root.begin() as conn:
            conn.execute(CreateSchema(schema))
        created_schema = True
        with patch.dict(os.environ, environment), patch.object(database, "engine", engine), \
                patch.object(database, "SessionLocal", sessionmaker(engine, autoflush=False)):
            import torch
            torch.set_num_threads(args.threads)
            Base.metadata.create_all(engine)
            model = SentenceTransformer(plan["model"], revision=REVISION, local_files_only=True, device="cpu")
            if model[0].auto_model.config._commit_hash != REVISION:
                raise ValueError("Model revision mismatch")
            search_service._embedding_model = model
            elastic = ElasticsearchProjectionIndex(target)
            qdrant = QdrantClient(url=environment["QDRANT_URL"], check_compatibility=False)
            vector_index = QdrantProjectionIndex(target, 384, qdrant)
            report["versions"] = {"elasticsearch": elastic.request("GET", "")["version"]["number"],
                                  "qdrant": qdrant.info().version}
            corpus = []
            for source in sources:
                path = ROOT / source["file_path"]
                if sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                    raise ValueError("Caption content changed")
                raw = json.dumps(passages(path)).encode()
                video = str(uuid5(NAMESPACE_URL, source["video_key"]))
                with database.create_session() as db:
                    db.add(Video(video_id=video, title=source["video_key"], owner_user_id="quality-owner",
                        status="processed", segments_status="completed", segments_path=source["video_key"] + ".json"))
                    db.commit()
                    revision = repo.register_revision(db, video, source["video_key"] + ".json", raw)
                    revision_id = revision.revision_id
                    db.commit()
                    jobs = [(j.job_id, j.generation_id) for j in db.query(SearchProjection).filter_by(video_id=video).all()]
                snapshot = TranscriptSnapshot.from_manifest(video, revision_id, raw)
                vectors = model.encode([p.text for p in snapshot.passages], normalize_embeddings=True, batch_size=32).tolist()
                for job, generation in jobs:
                    with database.create_session() as db:
                        attempt = repo.claim(db, job)
                    index, embeddings = (elastic, None) if generation == "lexical-v1" else (vector_index, vectors)
                    index.write(snapshot, attempt, embeddings)
                    if not index.verify(snapshot, attempt, embeddings):
                        raise ValueError("Projection verification failed")
                    with database.create_session() as db:
                        repo.publish(db, job, attempt)
                corpus.extend(p.as_hit() for p in snapshot.passages)
                print("Indexed", source["video_key"], flush=True)
            report["passages"] = len(corpus)
            report["corpus_sha256"] = sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()
            write_json(args.output / "corpus.json", corpus)
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_current_user] = lambda: AuthUser("quality-owner")
            original = library_search.search_library
            rng = random.Random(SEED)
            with TestClient(app) as client:
                def request(query, profile, round_index):
                    captured = []
                    def execute(owner, spec, deadline, **kwargs):
                        return original(owner, spec, deadline,
                            retriever=partial(library_search.retrieve, lexical_profile=profile),
                            candidate_observer=captured.append, **kwargs)
                    start = perf_counter()
                    with patch.object(library_search, "search_library", side_effect=execute):
                        response = client.post("/search", json={"query": query["query"], "mode": "hybrid", "limit": 5})
                    elapsed = (perf_counter() - start) * 1000
                    payload = response.json()
                    row = {"query_id": query["query_id"], "query": query["query"],
                        "query_type": query["query_type"], "profile": profile, "round": round_index,
                        "latency_ms": elapsed, "http_status": response.status_code,
                        "degraded": payload.get("degraded", False), "coverage": payload.get("coverage", {}),
                        "results": payload.get("results", [])}
                    if round_index == 0:
                        row["candidates"] = captured[0] if len(captured) == 1 else None
                    return row
                report["warmups"] = [request(queries[0], p, -1) for p in PROFILES]
                for n in range(args.rounds):
                    order = list(queries)
                    rng.shuffle(order)
                    for i, query in enumerate(order):
                        profiles = list(PROFILES)
                        rng.shuffle(profiles)
                        for profile in profiles:
                            report["rows"].append(request(query, profile, n))
                        if (i + 1) % 10 == 0:
                            print("Round", n + 1, "queries", i + 1, "/", len(queries), flush=True)
                    write_json(args.output / "results.json", report)
            report["completed"] = True
    finally:
        for name, operation in (
            ("elasticsearch", lambda: elastic.request("DELETE", target) if elastic else None),
            ("qdrant", lambda: qdrant.delete_collection(target) if qdrant and qdrant.collection_exists(target) else None)):
            try:
                operation()
                report["cleanup"][name] = "clean"
            except Exception as exc:
                report["cleanup"][name] = type(exc).__name__
        if elastic:
            elastic.close()
        if qdrant:
            qdrant.close()
        engine.dispose()
        try:
            if created_schema:
                with root.begin() as conn:
                    conn.execute(DropSchema(schema, cascade=True))
            report["cleanup"]["postgres"] = "clean"
        except Exception as exc:
            report["cleanup"]["postgres"] = type(exc).__name__
        root.dispose()
        search_service._embedding_model = old_model
        search_observability.logger.setLevel(old_level)
        report["summary"] = summarize(report)
        write_json(args.output / "results.json", report)
        write_json(args.output / "summary.json", report["summary"])
    print(json.dumps(report["summary"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--query-limit", type=int, default=150)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.query_limit <= 150 or not 1 <= args.rounds <= 3 or not 1 <= args.threads <= 8:
        parser.error("Expected 1..150 queries, 1..3 rounds, 1..8 threads")
    run(args)


if __name__ == "__main__":
    main()
