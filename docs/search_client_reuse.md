# Library search client reuse

Library semantic retrieval previously constructed and closed a Qdrant REST
client for each request. Within-video search already reused its ingestion client;
that path is unchanged. Library retrieval now leases a process-local client keyed
by connection settings. Initialization is synchronized, concurrent requests share
the transport, and shutdown waits for active leases before closing clients.
Forked children discard inherited client references and locks.

Each query passes its own remaining timeout without changing shared configuration.
The installed SDK rounds REST timeout values to whole seconds; the application
continues checking its original absolute deadline before and after retrieval.
Ownership/revision/attempt filters, embeddings, candidate depth and fusion are
unchanged. No feature flags or AGENTS.md edits accompany this change.

Validation: 38 existing library/hybrid tests completed successfully (6 skipped),
plus 4 new client lifecycle tests passed. Python compilation passed. Tests cover
concurrent first acquisition, release after exceptions, constructor retry and
shutdown with an active lease. Local Python is 3.12; CI targets 3.11.

## Paired benchmark

The active run is `tmp/search-client-reuse-02`. It uses disposable local
Postgres/Elasticsearch/Qdrant services, the original 500 queries and 50-video
caption corpus. Original and reused client implementations run in randomized
paired order for semantic, lexical and hybrid modes over two rounds: 6,000 API
requests. The lexical mode is an unchanged control. Fixed test authentication
excludes JWT/ingress/TLS overhead. This is not a concurrent-load benchmark.

The original retrieval implementation is preserved in
`tmp/library_search_before_client_reuse.py`; runner and source hashes are saved.
The runner owns temporary schema/index cleanup and closes pooled clients.
`tmp/finish_client_reuse.py` waits for completion and invokes
`benchmarks/summarize_client_reuse.py` without further AI calls.

Expected completion artifacts: `comparison.json`, `quality_scores.json`,
`REPORT.md`, and `completion.log` in the run directory. Check `completion.err`
if a report is absent. Do not infer completion from a partial results file.

The summary compares every before/after ordered result list, reports p50/p95,
HTTP/degraded counts, and scores first-round quality against the original
user-accepted labels. Missing labels remain unknown. No rejudging or context
expansion is part of this experiment. No latency improvement is claimed until
the paired run completes and these checks are inspected.
