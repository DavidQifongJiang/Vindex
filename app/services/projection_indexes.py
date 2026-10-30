"""Attempt-isolated physical indexes. Publication belongs exclusively to Postgres."""
import math
import re
import json

import requests
from qdrant_client import models as qm
from qdrant_client.http.exceptions import UnexpectedResponse

from app.services.elasticsearch_service import ElasticsearchSearch, IndexNotReady
from app.services.search_contracts import physical_point_id
from app.services.processing_errors import PermanentProcessingError, TemporaryProcessingError


def target_name(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,199}", value):
        raise ValueError("Index target must be a concrete lowercase name")
    return value


def documents(snapshot, attempt):
    return [dict(p.as_hit(), projection_id=attempt) for p in snapshot.passages]


class ElasticsearchProjectionIndex(ElasticsearchSearch):
    def __init__(self, target, url=None):
        self.target = target_name(target)
        super().__init__(url)

    def ensure(self):
        try:
            self.request("PUT", self.target, json={"settings": {"number_of_shards": 1, "number_of_replicas": 0},
                "mappings": {"dynamic": "strict", "_meta": {"vindex_projection_schema": 1}, "properties": {
                    **{key: {"type": "keyword"} for key in ("video_id", "revision_id", "passage_id", "projection_id")},
                    "ordinal": {"type": "integer"}, "start": {"type": "double"}, "end": {"type": "double"},
                    "text": {"type": "text", "analyzer": "standard"}}}})
        except requests.HTTPError as exc:
            if (exc.response is None or exc.response.status_code != 400
                    or exc.response.json().get("error", {}).get("type") != "resource_already_exists_exception"):
                raise
        mapping = self.request("GET", self.target + "/_mapping")[self.target]["mappings"]
        if mapping.get("_meta", {}).get("vindex_projection_schema") != 1:
            raise ValueError("Incompatible Elasticsearch projection mapping")

    @staticmethod
    def query(attempt):
        return {"term": {"projection_id": attempt}}

    def write(self, snapshot, attempt, vectors=None, checkpoint=lambda: None):
        self.ensure()
        for offset in range(0, len(snapshot.passages), 250):
            checkpoint()
            lines = []
            for passage in snapshot.passages[offset:offset + 250]:
                lines.extend([json.dumps({"index": {"_id": physical_point_id(attempt, passage.passage_id)}}),
                              json.dumps(dict(passage.as_hit(), projection_id=attempt), allow_nan=False)])
            result = self.request("POST", self.target + "/_bulk", data="\n".join(lines) + "\n",
                                  headers={"Content-Type": "application/x-ndjson"})
            if result.get("errors"):
                statuses = [next(iter(item.values())).get("status", 500) for item in result.get("items", [])]
                if any(400 <= status < 500 and status not in {408, 409, 429} for status in statuses):
                    raise PermanentProcessingError("Invalid lexical document", error_code="lexical_bulk_invalid")
                raise TemporaryProcessingError("Partial lexical bulk write", error_code="lexical_bulk_partial")
        checkpoint()
        self.refresh()

    def refresh(self):
        result = self.request("POST", self.target + "/_refresh")
        if result.get("_shards", {}).get("failed", 0):
            raise TemporaryProcessingError("Lexical refresh failed", error_code="lexical_refresh_failed")

    def verify(self, snapshot, attempt, vectors=None, checkpoint=lambda: None):
        try:
            count = self.request("POST", self.target + "/_count", json={"query": self.query(attempt)})
            if count.get("_shards", {}).get("failed", 0):
                raise TemporaryProcessingError("Incomplete count", error_code="lexical_verify_failed")
            if count["count"] != len(snapshot.passages):
                return False
            for offset in range(0, len(snapshot.passages), 250):
                checkpoint()
                passages = snapshot.passages[offset:offset + 250]
                result = self.request("POST", self.target + "/_mget", json={
                    "ids": [physical_point_id(attempt, p.passage_id) for p in passages]})
                if len(result["docs"]) != len(passages):
                    return False
                for actual, passage in zip(result["docs"], passages):
                    if actual.get("error"):
                        raise TemporaryProcessingError("Document read failed", error_code="lexical_verify_failed")
                    if not actual.get("found") or actual.get("_source") != dict(passage.as_hit(), projection_id=attempt):
                        return False
            return True
        except IndexNotReady:
            return False

    def delete(self, attempt):
        try:
            # Refresh BEFORE delete-by-query: an old searchable version can otherwise
            # conflict with the realtime document version even without concurrent work.
            self.refresh()
            result = self.request("POST", self.target + "/_delete_by_query", params={"refresh": "true"},
                                  json={"query": self.query(attempt)})
            if result.get("failures") or result.get("version_conflicts") or result.get("timed_out"):
                raise TemporaryProcessingError("Incomplete lexical cleanup", error_code="lexical_cleanup_failed")
            count = self.request("POST", self.target + "/_count", json={"query": self.query(attempt)})
            if count["count"] or count.get("_shards", {}).get("failed", 0):
                raise TemporaryProcessingError("Lexical cleanup not verified", error_code="lexical_cleanup_failed")
        except IndexNotReady:
            pass

    def search(self, attempt, query, limit=50):
        result = self.request("POST", self.target + "/_search", params={"allow_partial_search_results": "false"},
            json={"size": limit, "query": {"bool": {"filter": [self.query(attempt)],
                "must": [{"match": {"text": query}}]}}, "sort": [{"_score": "desc"}, {"ordinal": "asc"}]})
        if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
            raise TemporaryProcessingError("Incomplete lexical search", error_code="lexical_search_failed")
        return [dict(item["_source"], score=item["_score"]) for item in result["hits"]["hits"]]


class QdrantProjectionIndex:
    def __init__(self, target, dimension, client):
        self.target, self.dimension, self.client = target_name(target), dimension, client

    def close(self):
        pass  # Shared client is owned by the application.

    def ensure(self):
        if not self.client.collection_exists(self.target):
            try:
                self.client.create_collection(self.target, vectors_config=qm.VectorParams(size=self.dimension, distance=qm.Distance.COSINE))
            except UnexpectedResponse as exc:
                if exc.status_code != 409:
                    raise
        config = self.client.get_collection(self.target).config.params.vectors
        if not isinstance(config, qm.VectorParams) or config.size != self.dimension or config.distance != qm.Distance.COSINE:
            raise ValueError("Incompatible Qdrant projection vector schema")
        self.client.create_payload_index(self.target, "projection_id", qm.PayloadSchemaType.KEYWORD, wait=True)

    @staticmethod
    def query(attempt):
        return qm.Filter(must=[qm.FieldCondition(key="projection_id", match=qm.MatchValue(value=attempt))])

    def write(self, snapshot, attempt, vectors=None, checkpoint=lambda: None):
        if vectors is None or len(vectors) != len(snapshot.passages):
            raise ValueError("Missing matching embeddings")
        self.ensure()
        for offset in range(0, len(snapshot.passages), 250):
            checkpoint()
            points = [qm.PointStruct(id=physical_point_id(attempt, p.passage_id), vector=vector,
                       payload=dict(p.as_hit(), projection_id=attempt))
                      for p, vector in zip(snapshot.passages[offset:offset + 250], vectors[offset:offset + 250])]
            self.client.upsert(self.target, points=points, wait=True)
        checkpoint()

    def verify(self, snapshot, attempt, vectors=None, checkpoint=lambda: None):
        if not self.client.collection_exists(self.target):
            return False
        if self.client.count(self.target, count_filter=self.query(attempt), exact=True).count != len(snapshot.passages):
            return False
        for offset in range(0, len(snapshot.passages), 250):
            checkpoint()
            passages = snapshot.passages[offset:offset + 250]
            points = self.client.retrieve(self.target, ids=[physical_point_id(attempt, p.passage_id) for p in passages],
                                          with_payload=True, with_vectors=True)
            actual = {str(p.id): p for p in points}
            for i, passage in enumerate(passages, offset):
                point = actual.get(physical_point_id(attempt, passage.passage_id))
                if point is None or point.payload != dict(passage.as_hit(), projection_id=attempt):
                    return False
                if vectors is not None:
                    # Cosine collections normalize on ingestion; compare normalized vectors.
                    vector = vectors[i]
                    norm = math.sqrt(sum(v * v for v in vector))
                    normalized = [v / norm for v in vector] if norm else vector
                    if len(point.vector) != len(normalized) or any(not math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-6)
                            for a, b in zip(point.vector, normalized)):
                        return False
        return True

    def delete(self, attempt):
        if self.client.collection_exists(self.target):
            self.client.delete(self.target, points_selector=qm.FilterSelector(filter=self.query(attempt)), wait=True)
            if self.client.count(self.target, count_filter=self.query(attempt), exact=True).count:
                raise TemporaryProcessingError("Qdrant cleanup not verified", error_code="semantic_cleanup_failed")


def aligned_embeddings(segments, embeddings, settings):
    """Reuse only an unambiguous source match with compatible model provenance."""
    if not isinstance(embeddings, list) or len(segments) != len(embeddings):
        raise ValueError("Embedding manifest count mismatch")
    use_ids = all(item.get("segment_id") is not None for item in segments + embeddings)
    def key(item):
        return str(item["segment_id"]) if use_ids else (item["start"], item["end"], item["text"])
    indexed = {}
    for item in embeddings:
        identity = key(item)
        if identity in indexed:
            raise ValueError("Ambiguous legacy embedding identity")
        indexed[identity] = item
    vectors, seen = [], set()
    for source in segments:
        identity = key(source)
        if identity in seen or identity not in indexed:
            raise ValueError("Ambiguous source/embedding association")
        seen.add(identity)
        item = indexed[identity]
        if any(item[field] != source[field] for field in ("start", "end", "text")):
            raise ValueError("Embedding text/timestamps do not match accepted source")
        if (item.get("embedding_model"), item.get("embedding_model_revision")) != (settings["model"], settings["model_revision"]):
            raise ValueError("Embedding model provenance does not match generation; regenerate or explicitly annotate verified artifacts")
        vector = item["embedding"]
        if (not isinstance(vector, list) or len(vector) != settings["dimension"]
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
                or not any(vector)):
            raise ValueError("Invalid embedding vector")
        vectors.append(vector)
    return vectors
