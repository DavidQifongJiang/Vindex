"""Offline, pinned cross-encoder experiment over frozen authorized candidates."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import random
from time import perf_counter

from app.services.search_ranking import rerank_candidates
from benchmarks.candidate_diagnostics import analyze, content_key, human_labels
from benchmarks.score_search_metrics import primary_bounds, aggregate

MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "vocab.txt")
DEPTHS = (20, 50, 100)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=Path("benchmarks/model_cache/reranker-minilm-l6"))
    parser.add_argument("--download", action="store_true", help="Fetch the pinned official model (~92 MB) before timing")
    args = parser.parse_args()
    raw_path = args.run / "results.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    corpus_path = args.run / "corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    label_path = Path("benchmarks/library_queries_500/human_review_session.json")
    labels = human_labels(label_path)
    diagnostics, _ = analyze(raw, labels, corpus)
    if diagnostics["request_failures"]:
        raise ValueError("Repair incomplete candidate capture before reranking")
    rows = [r for r in raw["rows"] if r["round"] == 0 and r["profile"] == "baseline"]
    args.output.mkdir(parents=True, exist_ok=False)
    if args.download:
        from huggingface_hub import snapshot_download
        snapshot_download(MODEL, revision=REVISION, local_dir=args.model_dir,
                          allow_patterns=list(FILES), token=False)
    if not all((args.model_dir / name).is_file() for name in FILES):
        raise ValueError("Pinned model files missing; use --download explicitly")
    plan = {"model": MODEL, "revision": REVISION, "depths": list(DEPTHS), "seed": 20260917,
        "threads": 2, "batch_size": 16, "max_length": 512, "candidate_profile": "baseline",
        "source_sha256": sha256(raw_path.read_bytes()).hexdigest(),
        "corpus_sha256": sha256(corpus_path.read_bytes()).hexdigest(),
        "human_session_sha256": sha256(label_path.read_bytes()).hexdigest(),
        "file_hashes": {name: sha256((args.model_dir / name).read_bytes()).hexdigest() for name in FILES},
        "code_hashes": {name: sha256(Path(name).read_bytes()).hexdigest() for name in (
            "benchmarks/rerank_candidates.py", "app/services/search_ranking.py")},
        "query_ids": [r["query_id"] for r in rows],
        "limits": ["Offline development experiment, no serving change or promotion.",
                   "Rerank-only CPU time, not end-to-end API latency; one measurement per depth/query.",
                   "Source corpus uses captions; existing labels are sparse. No quality gate can pass.",
                   "Truncation to 512 query+passage tokens is fixed; no query rewriting."]}
    (args.output / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    import numpy as np
    import torch
    from sentence_transformers import CrossEncoder
    torch.set_num_threads(plan["threads"])
    model = CrossEncoder(str(args.model_dir), local_files_only=True, trust_remote_code=False,
                         device="cpu", max_length=plan["max_length"], model_kwargs={"use_safetensors": True})
    def predict(pairs):
        return model.predict(pairs, batch_size=plan["batch_size"], show_progress_bar=False)
    predict([("warmup", "A short passage for model warmup.")])
    rng = random.Random(plan["seed"])
    rng.shuffle(rows)
    results, judgments = [], []
    for i, row in enumerate(rows):
        depths = list(DEPTHS)
        rng.shuffle(depths)
        ranking = row["candidates"]["rankings"]
        for depth in depths:
            start = perf_counter()
            hits = rerank_candidates(row["query"], ranking["semantic"], ranking["lexical"], predict, depth=depth)
            elapsed = (perf_counter() - start) * 1000
            results.append({"query_id": row["query_id"], "query": row["query"], "query_type": row["query_type"],
                            "depth": depth, "rerank_ms": elapsed, "results": hits})
            if row["query_type"] != "no_answer_candidate":
                judgments.append({"depth": depth, "bounds": primary_bounds([
                    labels.get(content_key(row["query"], h), {}).get("rating") for h in hits])})
        if (i + 1) % 10 == 0:
            print("Reranked", i + 1, "/", len(rows), flush=True)
    summary = {"release_gate": "BLOCKED: sparse human labels and development-only caption corpus", "depths": {}}
    for depth in DEPTHS:
        times = [r["rerank_ms"] for r in results if r["depth"] == depth]
        summary["depths"][str(depth)] = {"requests": len(times), "p50_rerank_ms": float(np.percentile(times, 50)),
            "p95_rerank_ms": float(np.percentile(times, 95)),
            "human_only_quality_bounds": aggregate([j for j in judgments if j["depth"] == depth])}
    (args.output / "results.json").write_text(json.dumps({"plan": plan, "rows": results, "summary": summary}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
