"""Pure, bounded candidate selection and reranking for offline experiments.

Callers must supply authorized candidates and revalidate before serving results.
No model is loaded and no serving behavior is enabled by this module.
"""
from itertools import zip_longest
import math
from numbers import Real

from app.services.hybrid_search import reciprocal_rank_fusion


def balanced_candidates(semantic, lexical, depth=50):
    if type(depth) is not int or not 1 <= depth <= 100:
        raise ValueError("Reranking depth must be 1..100")
    if len(semantic) > 50 or len(lexical) > 50:
        raise ValueError("At most 50 candidates per engine")
    union = reciprocal_rank_fusion(semantic, lexical, limit=100)
    canonical = {h["passage_id"]: h for h in union}
    selected, seen = [], set()
    # Taking alternating unique hits prevents a shallow budget from excluding an
    # entire engine. Unlike RRF top-five preselection, single-engine hits survive.
    for pair in zip_longest(semantic, lexical):
        for hit in pair:
            if hit is None or hit["passage_id"] in seen:
                continue
            seen.add(hit["passage_id"])
            selected.append(canonical[hit["passage_id"]])
            if len(selected) == depth:
                return selected
    return selected


def rerank_candidates(query, semantic, lexical, predict, *, depth=50, limit=5):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
        raise ValueError("Query must contain 1..500 characters")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Result limit must be 1..20")
    candidates = balanced_candidates(semantic, lexical, depth)
    if not candidates:
        return []
    scores = list(predict([(query, h["text"]) for h in candidates]))
    if len(scores) != len(candidates) or any(
            isinstance(s, bool) or not isinstance(s, Real) or not math.isfinite(float(s)) for s in scores):
        raise ValueError("Reranker returned invalid scores")
    ranked = [dict(hit, score=float(score)) for hit, score in zip(candidates, scores)]
    return sorted(ranked, key=lambda h: (-h["score"], h["passage_id"]))[:limit]
