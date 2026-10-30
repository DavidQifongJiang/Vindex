# Retrieval experiments: BM25, hybrid, and Elasticsearch

## Benchmark audit

The existing baseline contains 50 queries: 5 on a short clip and 45 on the
full sleep talk. These share source material. The expanded benchmark contains
100 queries across ten 15-minute algorithm lectures. Existing expanded results:
Hit@1 65%, Hit@5 90%, Precision@5 43.4%, MRR 0.7457.

Labels are manually derived from source transcripts. Relevance is timestamp
overlap OR a start timestamp within the label's tolerance (default 30 seconds).
Precision uses a denominator of five; timestamp errors exclude misses. These
are useful regression fixtures, not independently adjudicated relevance labels
or a held-out test set. Neither set establishes rejection of unanswerable queries.
The saved top-five results are not a sufficient corpus for evaluating BM25.

`expanded_15min_corpus.json` freezes the complete accepted segments exported from
the existing local database and S3 artifacts for the exact ten historical video
IDs. No transcription, chunking, or text edits were made. The export was read-only
against existing Vindex data. The paired run regenerated document embeddings with
the locally cached all-MiniLM-L6-v2 model; the report records its revision and
hashes of the corpus and labels. The exact dense baseline reproduces all four
historical ranking metrics, supporting comparability of the quality evaluation.

## Full 100-query results

| Retrieval | Hit@1 | Hit@5 | Precision@5 | MRR |
|---|---:|---:|---:|---:|
| Dense exact cosine | 65% | 90% | 43.4% | 0.7457 |
| Python BM25 | 74% | 98% | 53.0% | 0.8368 |
| Dense + Python BM25 RRF | 64% | 95% | 52.2% | 0.7767 |
| Elasticsearch BM25 | 74% | 99% | 53.2% | 0.8393 |
| Dense + Elasticsearch BM25 RRF | 65% | 95% | 52.2% | 0.7808 |

BM25 improves this lexical-heavy benchmark substantially. Equal-weight RRF
improves top-five recall over dense retrieval but does not improve first-result
accuracy. Elasticsearch retrieves one additional top-five hit over Python BM25;
that small difference alone does not establish a reason to adopt it operationally.
Lucene's tokenization, floating-point scoring and encoded length norms can differ
from the Python implementation despite matched k1/b and analyzer intent.

No parameters were tuned against these results. Keep the existing semantic
default until a separate development set and held-out queries justify a change.
Include paraphrases, exact identifiers/numbers, terminology, unanswerable queries,
and adjudicated relevant passages in the next evaluation.

The report contains per-query results and P95 latency/timestamp errors. Warm local
retrieval P95 was roughly 12 ms dense, 2.4 ms Python BM25, 14.9 ms Python hybrid,
15.9 ms Elasticsearch BM25, and 23.6 ms Elasticsearch hybrid. These are **not API
latencies or throughput measurements**: offline runs exclude corpus loading,
document embedding, and index creation. Elasticsearch timings include local HTTP
and readiness lookup; Python BM25 uses a prebuilt in-memory index. The API BM25
path currently includes S3 loading and index construction. The old API's internal
latency also excludes synchronous metrics persistence; do not compare it directly.

An earlier 45-query exploratory run is retained in
`local_medium_retrieval_comparison.json`. It uses an older complete local
transcript and explicitly excludes the five short-clip labels. It is not the
original 50-query baseline and is not combined with the expanded run.

## Implementation

The existing default remains `qdrant_embedding`. New API modes:

- `bm25`: per-video Python BM25, k1=1.2, b=0.75, Unicode word tokens, lowercase,
  no stemming or stopword removal. No new Python dependency.
- `hybrid`: top 50 accepted Qdrant candidates plus top 50 BM25 candidates,
  equal-weight reciprocal rank fusion with k=60, returning five results.
- `elasticsearch_bm25`: an explicitly prepared immutable Elasticsearch snapshot.
- `elasticsearch_hybrid`: the same Qdrant branch fused with Elasticsearch BM25.

Fusion uses `(start, end, text)` identity because historical Qdrant payloads lack
segment IDs. It deduplicates each ranking and never adds incomparable raw scores.
The API still authorizes access before contacting either lexical backend. Dense
search preserves the accepted-attempt filter. BM25 reads the accepted merged
segment manifest. Search rejects videos marked deleting.

Elasticsearch is an **optional experimental projection**, not a new dependency
of uploads or Celery processing. Each `(video_id, segments_path)` revision gets
an immutable index; queries require a successfully completed bulk load and
refresh. This preserves per-video BM25 statistics and avoids mixing unrelated
videos' IDF. It creates many shards and is not a production topology for a large
library. Bulk errors leave the index unreadable; the same content can retry.
Content changes require a new revision. No silent fallback masks an outage.

Index preparation is explicit through the benchmark runner. New production
uploads do not automatically get an Elasticsearch projection. The API returns
409 until the current revision has been prepared. With ELASTICSEARCH_URL enabled,
video deletion removes the current snapshot before existing artifact cleanup.
Old experimental revisions require explicit retirement; there is no automatic
Elasticsearch GC/reconciliation service. Do not enable this as the default backend
without designing and testing that lifecycle.

## Reproduce

Commands run from the repository root. Use the project's Python environment.

```powershell
docker compose -f docker-compose.search.yml up -d
python -m benchmarks.compare_retrieval --corpus benchmarks/expanded_15min_corpus.json --labels benchmarks/semantic_search_labels_expanded_15min.csv --algorithms dense,bm25,hybrid,elasticsearch_bm25,elasticsearch_hybrid --elasticsearch-url http://127.0.0.1:9200 --prepare-elasticsearch --output benchmarks/expanded_15min_retrieval_comparison.json
```

The Compose file starts Elasticsearch 9.3.1 with a 512 MiB heap, 2 GiB memory
limit, persistent experimental volume, and a loopback-only HTTP port. Authentication
is disabled **only for this isolated local experiment**. Do not expose it remotely.
Stop only the experimental service with:

```powershell
docker compose -f docker-compose.search.yml stop elasticsearch
```

The model must already be cached; the runner deliberately uses local-files-only.
Output order is randomized with seed 42, retrieval parameters are fixed, and
missing corpora cause an error rather than silently dropping queries. `--only`
explicitly selects a subset and records exclusions. Reports contain full results,
so treat exports/results from private videos as private data.

To export updated complete segments through an authorized API:

```powershell
# Set VINDEX_BENCHMARK_TOKEN externally if authentication is required.
python -m benchmarks.export_search_corpus --config benchmarks/expanded_15min_search_quality_config.json --api-base-url http://localhost:8000 --output benchmarks/new_corpus.json
```

To benchmark the **updated API**, first deploy this code and prepare matching
Elasticsearch revisions. Configure ELASTICSEARCH_URL in the API environment
(`http://host.docker.internal:9200` for Docker Desktop accessing the local experiment).
Then add `--api-base-url http://localhost:8000` to the comparison command. API mode
checks manifest identity and measures full HTTP request latency. The original
single-algorithm benchmark runner also accepts the new algorithm names and now
labels their configuration correctly.

For a remote secured cluster, set ELASTICSEARCH_API_KEY and optionally
ELASTICSEARCH_CA_CERT; TLS verification is not disabled by the client.

Run tests:

```powershell
python -m unittest discover -s tests
$env:VINDEX_TEST_ELASTICSEARCH_URL = 'http://127.0.0.1:9200'
python -m unittest tests.test_hybrid_search
```

The optional real Elasticsearch test creates and deletes only its own random
snapshot. Other tests cover BM25 math, empty/no-match queries, RRF deduplication,
authorization before dependencies, accepted dense attempt tokens, and partial
bulk publication failure.
