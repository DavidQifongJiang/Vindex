import os
from uuid import uuid5, NAMESPACE_DNS

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FilterSelector,
    FieldCondition,
    MatchValue,
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
        )

    return _client


def ensure_collection():
    client = get_qdrant_client()

    collections = client.get_collections().collections
    collection_names = [collection.name for collection in collections]

    if COLLECTION_NAME not in collection_names:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE
            )
        )


def upsert_segments(video_id: str, segment_embeddings: list[dict]):
    ensure_collection()
    client = get_qdrant_client()

    points = []

    for index, segment in enumerate(segment_embeddings):
        points.append(
            PointStruct(
                id=str(uuid5(NAMESPACE_DNS, f"{video_id}:{index}")),
                vector=segment["embedding"],
                payload={
                    "video_id": video_id,
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"],
                }
            )
        )

    client.upsert(
        collection_name=COLLECTION_NAME,
        points=points
    )


def search_segments(query_embedding: list[float], video_id: str, top_k: int = 5):
    ensure_collection()
    client = get_qdrant_client()

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        query_filter=Filter(
            must=[
                FieldCondition(
                    key="video_id",
                    match=MatchValue(value=video_id)
                )
            ]
        ),
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
