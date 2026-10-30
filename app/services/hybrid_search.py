"""Per-video BM25 and deterministic reciprocal rank fusion; no model imports."""
from collections import Counter
import math
import re

from app.services.search_contracts import IDENTITY_FIELDS, Passage

CANDIDATES = 50
RRF_K = 60


def tokens(text):
    return re.findall(r"\b\w+\b", text.lower())


def segment_key(segment):
    # Qdrant's historical payloads do not include segment_id.
    return (float(segment["start"]), float(segment["end"]), segment["text"])


class BM25:
    def __init__(self, segments, k1=1.2, b=0.75):
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("Invalid BM25 parameters")
        self.segments = list(segments)
        self.k1, self.b = k1, b
        self.terms = [Counter(tokens(s["text"])) for s in self.segments]
        self.lengths = [sum(t.values()) for t in self.terms]
        self.average = sum(self.lengths) / max(1, len(self.lengths))
        self.df = Counter(term for terms in self.terms for term in terms)

    def search(self, query, limit=5):
        result = []
        query_terms = set(tokens(query))
        for segment, terms, length in zip(self.segments, self.terms, self.lengths):
            score = 0.0
            for term in query_terms:
                tf = terms[term]
                if not tf:
                    continue
                idf = math.log1p((len(self.segments) - self.df[term] + .5) / (self.df[term] + .5))
                norm = self.k1 * (1 - self.b + self.b * length / (self.average or 1))
                score += idf * tf * (self.k1 + 1) / (tf + norm)
            if score > 0:
                result.append({"start": segment["start"], "end": segment["end"],
                               "text": segment["text"], "score": score})
        return sorted(result, key=lambda s: (-s["score"], segment_key(s)))[:limit]


def reciprocal_rank_fusion(*rankings, limit=5, k=RRF_K):
    if type(k) not in (int, float) or not math.isfinite(k) or k < 0:
        raise ValueError("RRF k must be nonnegative")
    if type(limit) is not int or limit < 0:
        raise ValueError("Result limit must be a nonnegative integer")
    scores, documents = {}, {}
    canonical = None
    revisions = {}
    for ranking in rankings:
        seen = set()
        for rank, document in enumerate(ranking, 1):
            identified = any(field in document for field in IDENTITY_FIELDS)
            if canonical is not None and identified != canonical:
                raise ValueError("Cannot fuse canonical and legacy unscoped hits")
            canonical = identified
            if identified:
                passage = Passage.from_hit(document)
                previous_revision = revisions.setdefault(passage.video_id, passage.revision_id)
                if previous_revision != passage.revision_id:
                    raise ValueError("Cannot fuse different revisions of the same video")
                key = passage.passage_id
                clean = passage.as_hit()
                if key in documents and documents[key] != clean:
                    raise ValueError("Conflicting payloads for the same passage identity")
            else:
                key = segment_key(document)
                clean = {name: document[name] for name in ("start", "end", "text")}
            if key in seen:
                continue
            seen.add(key)
            documents[key] = clean
            scores[key] = scores.get(key, 0.0) + 1 / (k + rank)
    return [dict(documents[key], score=scores[key])
            for key in sorted(scores, key=lambda key: (-scores[key], key))[:limit]]
