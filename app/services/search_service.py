import json
import re
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

STOPWORDS = {
    "the", "is", "a", "an", "and", "or", "to", "of", "in", "on",
    "for", "with", "this", "that", "it", "as", "are", "was", "were",
    "be", "by", "from", "at", "we", "you", "i", "they", "he", "she",
    "what", "how", "why", "when", "where"
}


def build_segment_embeddings(segments):
    segment_embeddings = []

    for segment in segments:
        text = segment.get("text", "")
        embedding = embedding_model.encode(text).tolist()

        segment_embeddings.append({
            "start": segment.get("start"),
            "end": segment.get("end"),
            "text": text,
            "embedding": embedding
        })

    return segment_embeddings


def embedding_search(query: str, embedding_path: Path, top_k: int = 5):
    query_embedding = embedding_model.encode(query)

    with embedding_path.open("r", encoding="utf-8") as file:
        segment_embeddings = json.load(file)

    results = []

    for segment in segment_embeddings:
        score = cosine_similarity(query_embedding, segment["embedding"])

        results.append({
            "start": segment["start"],
            "end": segment["end"],
            "text": segment["text"],
            "score": score
        })

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:top_k]


def tokenize(text: str, remove_stopwords: bool = False) -> list[str]:
    words = re.findall(r"\b\w+\b", text.lower())

    if remove_stopwords:
        words = [word for word in words if word not in STOPWORDS]

    return words


def exact_score(query: str, text: str) -> int:
    return 1 if query.lower() in text.lower() else 0


def overlap_score(query: str, text: str) -> int:
    query_words = tokenize(query)
    text_words = tokenize(text)

    score = 0
    for word in query_words:
        if word in text_words:
            score += 1

    return score


def stopword_overlap_score(query: str, text: str) -> int:
    query_words = tokenize(query, remove_stopwords=True)
    text_words = tokenize(text, remove_stopwords=True)

    score = 0
    for word in query_words:
        if word in text_words:
            score += 1

    return score


def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)

    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def score_segment(query: str, text: str, algorithm: str) -> int:
    if algorithm == "exact":
        return exact_score(query, text)

    if algorithm == "overlap":
        return overlap_score(query, text)

    if algorithm == "stopword_overlap":
        return stopword_overlap_score(query, text)

    raise ValueError(f"Unsupported algorithm: {algorithm}")
