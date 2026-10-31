import os
from time import time
from uuid import uuid5, NAMESPACE_DNS

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FilterSelector,
    FieldCondition,
    MatchValue,
    MatchAny,
    IsEmptyCondition,
    PayloadField,
)

COLLECTION_NAME = "vindex_segments"
VECTOR_SIZE = 384  # all-MiniLM-L6-v2 embedding size

_client = None


def get_qdrant_client():
    global _client

    if _client is None:
        _client = QdrantClient(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=os.getenv("QDRANT_API_KEY"),
            check_compatibility=False,
            timeout=10,
        )

    return _client


def ensure_collection():
    client = get_qdrant_client()

    collections = client.get_collections().collections
    collection_names = [collection.name for collection in collections]

    if COLLECTION_NAME not in collection_names:
        try:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
        except UnexpectedResponse as exc:
            if exc.status_code != 409 or not client.collection_exists(COLLECTION_NAME):
                raise


def upsert_segments(video_id: str, segment_embeddings: list[dict], *, attempt_token: str | None = None):
    ensure_collection()
    client = get_qdrant_client()

    points = []

    for index, segment in enumerate(segment_embeddings):
        segment_id = segment.get("segment_id") or index
        payload = {
            "video_id": video_id,
            "start": segment["start"],
            "end": segment["end"],
            "text": segment["text"],
        }
        if segment.get("chunk_index") is not None:
            payload["chunk_index"] = segment["chunk_index"]
        if attempt_token is not None:
            payload["attempt_token"] = attempt_token
            payload["written_at_epoch"] = time()

        points.append(
            PointStruct(
                id=str(uuid5(NAMESPACE_DNS, f"{video_id}:{attempt_token}:{segment_id}" if attempt_token else f"{video_id}:{segment_id}")),
                vector=segment["embedding"],
                payload=payload,
            )
        )

    client.upsert(
        collection_name=COLLECTION_NAME,
        points=points,
        wait=True,
    )


def search_segments(query_embedding: list[float], video_id: str, top_k: int = 5, *, chunk_attempts=None):
    ensure_collection()
    client = get_qdrant_client()

    conditions = [FieldCondition(key="video_id", match=MatchValue(value=video_id))]
    if chunk_attempts is not None:
        tokens = [token for _, token in chunk_attempts if token]
        legacy_indexes = [index for index, token in chunk_attempts if not token]
        accepted = []
        if tokens:
            accepted.append(FieldCondition(key="attempt_token", match=MatchAny(any=tokens)))
        if legacy_indexes:
            accepted.append(Filter(must=[
                FieldCondition(key="chunk_index", match=MatchAny(any=legacy_indexes)),
                IsEmptyCondition(is_empty=PayloadField(key="attempt_token")),
            ]))
        if not accepted:
            return []
        conditions.append(Filter(should=accepted))

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        query_filter=Filter(must=conditions),
        limit=top_k,
    ).points

    return [
        {
            "start": result.payload["start"],
            "end": result.payload["end"],
            "text": result.payload["text"],
            "score": result.score
        }
        for result in results
    ]


def delete_segments(video_id: str):
    ensure_collection()
    client = get_qdrant_client()

    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(
            filter=Filter(
                must=[
                    FieldCondition(
                        key="video_id",
                        match=MatchValue(value=video_id),
                    )
                ]
            )
        ),
        wait=True,
    )
