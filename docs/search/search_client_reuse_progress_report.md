# Search latency optimization — interim report

Status at inspection: round 1 has completed at least 250 of 500 queries.
The experiment contains two rounds and six requests per query (three search
modes, each before and after client reuse). This represents at least 1,500 of
6,000 planned requests. The first complete-round checkpoint is not yet available.

## Engineering change

Library semantic search previously created and closed a Qdrant REST client for
each request. It now reuses a process-local transport, with synchronized
initialization, per-request timeout arguments, and shutdown that waits for
active leases. Ownership and revision filters, embeddings, ranking and fusion
remain unchanged. Within-video search already reused its client.

## Verification

36 focused tests passed; 6 integration tests were skipped. Python compilation
passed. Tests cover concurrent client acquisition, shutdown with an active
request, retry after constructor failure, and release after request exceptions.

## Measurement design

- Same 500 queries and 50-video caption corpus for both implementations.
- Semantic, lexical and hybrid modes; lexical is the unchanged control.
- Randomized paired before/after order over two sequential rounds.
- Real local PostgreSQL, Elasticsearch and Qdrant behind the ASGI API.
- Existing accepted relevance labels; no new model grading.
- Planned outputs: p50/p95 latency, HTTP/degraded counts, exact ordered-result
  equality, and Hit@1, Hit@5 and MRR using the original labels.

## Conclusions available now

The implementation and focused verification are complete. The latency experiment
is still running. No measured speedup or unchanged-quality result is claimed yet.
The earlier approximately 205 ms client-construction measurement motivated this
change; it is not a measured end-to-end saving from the new implementation.

Final artifacts will be saved under `tmp/search-client-reuse-02/`: `REPORT.md`,
`comparison.json`, and `quality_scores.json`. A partial run must not be presented
as the final 500-query comparison. Local measurements exclude ingress, TLS and
JWT verification and are not production-load measurements.
