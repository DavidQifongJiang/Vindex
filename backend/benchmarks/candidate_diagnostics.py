"""Blinded candidate review and human-only diagnostic bounds, never release approval."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import random
from statistics import mean

from app.services.hybrid_search import reciprocal_rank_fusion
from app.services.search_contracts import Passage
from benchmarks.score_search_metrics import primary_bounds, aggregate


def content_key(query, hit):
    """Reuse a judgment only for identical query, video, times, and passage text."""
    values = [query, hit["video_id"], hit["start"], hit["end"], hit["text"]]
    return sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def human_labels(path):
    labels = {}
    for row in json.loads(path.read_text(encoding="utf-8"))["items"]:
        if row.get("status") != "human_verified":
            continue
        grade = row["human_rating"]
        if type(grade) is not int or grade not in (0, 1, 2):
            raise ValueError("Invalid human grade")
        key = content_key(row["query"], row["candidate"]["passage"])
        if key in labels and labels[key]["rating"] != grade:
            raise ValueError("Conflicting verified judgments")
        labels[key] = {"rating": grade, "source_sample": row["sample_id"],
                       "provenance": "human_verified_exact_content"}
    return labels


def coverage_bounds(grades):
    if any(g is not None and (type(g) is not int or g not in (0, 1, 2)) for g in grades):
        raise ValueError("Invalid relevance grade")
    lower = int(2 in grades)
    upper = int(bool(lower) or None in grades)
    return {"lower": lower, "upper": upper, "value": lower if lower == upper else None}


def validate_corpus(raw, corpus):
    if sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest() != raw["corpus_sha256"]:
        raise ValueError("Frozen corpus hash mismatch")
    canonical = {h["passage_id"]: Passage.from_hit(h).as_hit() for h in corpus}
    if len(canonical) != len(corpus):
        raise ValueError("Duplicate corpus passage identities")
    for row in raw["rows"]:
        candidates = row.get("candidates") or {}
        for hits in candidates.get("rankings", {}).values():
            for hit in hits:
                clean = Passage.from_hit(hit).as_hit()
                if canonical.get(clean["passage_id"]) != clean:
                    raise ValueError("Candidate content differs from frozen corpus")
        for hit in row["results"]:
            normalized = dict(hit, start=hit.get("start", hit.get("start_seconds")),
                              end=hit.get("end", hit.get("end_seconds")))
            clean = Passage.from_hit(normalized).as_hit()
            if canonical.get(clean["passage_id"]) != clean:
                raise ValueError("Response content differs from frozen corpus")
    return canonical


def analyze(raw, labels, corpus):
    if not raw.get("completed") or not raw["summary"]["integrity_complete"]:
        raise ValueError("Incomplete experiment cannot be scored")
    # Independently verify the query/profile/round matrix rather than trusting a flag.
    plan = raw["plan"]
    query_rows = plan["queries"]
    if len({q["query_id"] for q in query_rows}) != len(query_rows):
        raise ValueError("Duplicate query identities")
    expected = {(q["query_id"], p, n) for q in query_rows for p in plan["profiles"] for n in range(plan["rounds"])}
    actual = [(r["query_id"], r["profile"], r["round"]) for r in raw["rows"]]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Missing or duplicate measurements")
    validate_corpus(raw, corpus)
    queries = {q["query_id"]: q for q in query_rows}
    pool, scores, coverage, errors = {}, [], [], []
    for row in raw["rows"]:
        query = queries[row["query_id"]]
        if row["query"] != query["query"] or row["query_type"] != query["query_type"]:
            raise ValueError("Query content changed")
        healthy = row["http_status"] == 200 and not row["degraded"] and row["coverage"].get("complete")
        if row["round"] != 0:
            if not healthy:
                errors.append({"query_id": row["query_id"], "profile": row["profile"], "round": row["round"]})
            continue
        capture = row.get("candidates")
        healthy = (healthy and capture and
                   all(capture["branches"].get(b, {}).get("status") == "ok" for b in ("semantic", "lexical")))
        if not healthy:
            errors.append({"query_id": row["query_id"], "profile": row["profile"], "round": row["round"]})
            # Retain failures in the denominator as unknown, never silently drop.
            if query["query_type"] != "no_answer_candidate":
                coverage.append({"query_id": row["query_id"], "profile": row["profile"],
                                 "lower": 0, "upper": 1, "value": None})
                for mode in ("semantic", "lexical", "hybrid"):
                    scores.append({"query_id": row["query_id"], "profile": row["profile"], "mode": mode,
                                   "bounds": primary_bounds([None] * 5)})
            continue
        rankings = capture["rankings"]
        if set(rankings) != {"semantic", "lexical"}:
            raise ValueError("Both healthy rankings must be explicitly captured")
        if any(len(rankings.get(b, [])) > 50 for b in ("semantic", "lexical")):
            raise ValueError("Candidate budget exceeded")
        union = reciprocal_rank_fusion(rankings.get("semantic", []), rankings.get("lexical", []), limit=100)
        top = {"semantic": rankings.get("semantic", [])[:5], "lexical": rankings.get("lexical", [])[:5],
               "hybrid": union[:5]}
        if [h["passage_id"] for h in row["results"]] != [h["passage_id"] for h in top["hybrid"]]:
            raise ValueError("Captured candidates do not reproduce serving results")
        for hit in union:
            key = content_key(query["query"], hit)
            entry = {"review_id": key, "query_id": query["query_id"], "query": query["query"],
                     "video_id": hit["video_id"], "start": hit["start"], "end": hit["end"], "text": hit["text"],
                     "rating": labels.get(key, {}).get("rating"),
                     "provenance": labels.get(key, {}).get("provenance", "unjudged")}
            if key in pool and pool[key] != entry:
                raise ValueError("Review identity conflict")
            pool[key] = entry
        if query["query_type"] != "no_answer_candidate":
            coverage.append({"query_id": query["query_id"], "profile": row["profile"],
                **coverage_bounds([labels.get(content_key(query["query"], h), {}).get("rating") for h in union])})
            for mode, hits in top.items():
                scores.append({"query_id": query["query_id"], "profile": row["profile"], "mode": mode,
                    "bounds": primary_bounds([labels.get(content_key(query["query"], h), {}).get("rating") for h in hits])})
    summary = {"release_gate": "BLOCKED", "query_count": len(queries), "request_failures": errors,
        "review_pairs": len(pool), "human_verified_pairs": sum(p["rating"] is not None for p in pool.values()),
        "limits": ["Development captions only; no untouched confirmation set or verified query-level answerability.",
                   "Bounds describe missing judgments, not statistical confidence. Unknown is not irrelevant.",
                   "Existing human labels reused only on identical query/video/timestamps/text.",
                   "No automated labels enter these metrics; errors remain in score denominators."],
        "profiles": {}}
    for profile in plan["profiles"]:
        rows = [r for r in coverage if r["profile"] == profile]
        summary["profiles"][profile] = {"candidate_answer_coverage_bounds": {
            "candidate_answer_seeking_queries": len(rows),
            "lower": mean(r["lower"] for r in rows) if rows else None,
            "upper": mean(r["upper"] for r in rows) if rows else None,
            "determined_queries": sum(r["value"] is not None for r in rows)},
            "top5_human_only": {mode: aggregate([r for r in scores if r["profile"] == profile and r["mode"] == mode])
                                for mode in ("semantic", "lexical", "hybrid")}}
    return summary, list(pool.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--human-session", type=Path,
        default=Path("benchmarks/library_queries_500/human_review_session.json"))
    args = parser.parse_args()
    results = args.run / "results.json"
    raw = json.loads(results.read_text(encoding="utf-8"))
    corpus_path = args.run / "corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    summary, pool = analyze(raw, human_labels(args.human_session), corpus)
    summary["hashes"] = {"results": sha256(results.read_bytes()).hexdigest(),
                         "corpus": sha256(corpus_path.read_bytes()).hexdigest(),
                         "human_session": sha256(args.human_session.read_bytes()).hexdigest()}
    args.output.mkdir(parents=True, exist_ok=False)
    random.Random(20260917).shuffle(pool)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (args.output / "review_pool.jsonl").open("w", encoding="utf-8") as stream:
        for row in pool:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    # Show a small deterministic, ungraded, engine-blinded batch first.
    pending = [r for r in pool if r["rating"] is None]
    lines = ["# Candidate review batch 1", "", "Development diagnosis only. Grade each passage: 0 = does not answer; "
             "1 = partial answer; 2 = answers; unsure = leave unknown. Judge the displayed text, not the lecture title.", ""]
    seen = set()
    for row in pending:
        if row["query_id"] in seen:
            continue
        seen.add(row["query_id"])
        lines.extend([f"## {len(seen)}. {row['query']}", "", row["text"], "",
                      f"Review ID: `{row['review_id']}`", "", "Rating: ___", ""])
        if len(seen) == 10:
            break
    (args.output / "review_batch_01.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("release_gate", "query_count", "review_pairs", "human_verified_pairs")}, indent=2))


if __name__ == "__main__":
    main()
