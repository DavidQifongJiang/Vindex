# Search failure analysis and proposed experiments

Date: 2026-09-17. Analysis only; no serving changes or demonstrated new gains.
Existing labels are the user-accepted working baseline, with original provenance
preserved. Detailed passage observations below are analyst diagnosis, not new
human judgments or silent corrections to that baseline.

## Evidence and scope

All 178 hybrid misses among 450 answer-seeking queries were classified by whether
an accepted answer appeared in either branch's top five. Counts: 48 semantic-only,
26 lexical-only, seven both, 97 neither. Thus 81 misses have an accepted answer
already retrieved in a branch's top five. This is not an 81-query promised gain:
changing fusion can also lose existing successes.

The CLI development capture has 47 hybrid misses among 135 positives. Of these,
24 have a known answer somewhere in the top-50 candidate union; 23 are unresolved.
Sparse labels cannot establish that the latter have no answer in the union.
The exposed full500 set is diagnosis/regression evidence, not an untouched holdout.

Reproduce these counts with `python -m benchmarks.audit_search_failures`.
Output: `tmp/search-quality-v2/failure-audit.json`, including source hashes and
per-query failure routing. Text inspection focused on selected cases, not a claim
that all 178 misses received a complete semantic root-cause review.

## 1. Fusion rewards agreement too strongly

Serving uses equal-weight RRF, k=60, and top50 per branch. A document ranked 50th
by both engines scores 2/(60+50)=.01818. A document ranked first by one engine and
missing from the other scores 1/(60+1)=.01639. Therefore every dual-list document
outranks every single-list document, regardless of how high that single rank is.
This is a consequence of the configuration, not a bug in the RRF implementation.

Concrete case: lq500_0092, "Double hashing probe sequence". In the CLI snapshot,
MIT 6.006 lecture10 at 2197.27 seconds gives h1(k)+i*h2(k) mod m. It ranks fourth
in semantic search, is absent from lexical top50, and falls to fusion rank15.
The preceding passage at 2174.59 names double hashing; it ranks semantic5 and
lexical2. The formula is split away from the terminology that would help ES find it.

First experiment: preserve the exact candidate pool and compare existing k60 with
k10 and k0, holding equal weights and candidate depth fixed. At k10, first in one
engine scores .0909 while 50th in both scores .0333, removing the unconditional
agreement dominance. This does not guarantee a better top5. Report paired rescued
and lost queries, Hit@1/5, MRR@5, and candidate-category regressions. Label newly
promoted passages before claiming complete scores. Do not combine this change
with chunking, query changes, or a new embedding model.

## 2. Passages separate the topic from its explanation

- lq500_0051, "AVL tree height balance invariant": lecture06 at 812.89 seconds
  says subtree heights differ by at most one. It is semantic rank18, absent from
  lexical top50, and fusion rank40. The higher-ranked 870.06 passage names AVL
  trees and promises a proof, but does not state the invariant. All tested
  reranker depths leave the 812.89 passage outside their top5.
- lq500_0037 asks why heap construction is linear despite logarithmic repair.
  Lecture04 splits the argument across 2345.11 (constant work near leaves),
  2374.59 (work at level L), 2405.48 (node counts), and 2436.76 seconds (fewer
  nodes as work increases). These fragments individually lack the full reasoning.
  Both retrieval and reranking favor a 3104.04 passage about heapsort's n log n
  complexity, which mentions heap construction but answers a different question.
- lq500_0054 asks how insertion imbalance is repaired. Lecture06 introduces the
  fix at 1775.39, describes rotations around 2021.64–2082.44, and connects a
  right rotation to the imbalance at 2117.44. A generic adjacent 30-second
  expansion cannot be assumed to reconstruct this whole explanation.

Second experiment: retain exact timestamped child passages for playback, but
build deterministic overlapping parent windows for retrieval context. Compare
60-second and 120-second windows with half-window stride, capped at video/revision
boundaries, without generated summaries. Rebuild both engines from the same new
manifest. Evaluate the displayed window as new text with new labels, and measure
timestamp usefulness and duplicate-window rate as well as answer quality.
This is a bounded probe, not a claim that either window duration is optimal.
Sentence-aware boundaries can follow if these probes demonstrate useful coverage.
Earlier blanket context expansion failed on eight examples; that result is why
new windows must be tested rather than assumed beneficial.

## 3. Previous lexical experiments rarely activated useful boosts

All four CLI profiles produced identical candidate ordering across 150 queries.
The phrase variant changed the DSL for 40 queries but changed no candidate scores.
Term variants changed scores on six queries and changed no rankings.

For unquoted short queries, the phrase code boosts the entire query. Thus
"Double hashing probe sequence" is tested as a whole phrase, not "double hashing".
The term boost requires every retained term in the same passage. A passage that
explains a concept through pronouns or formulas often cannot satisfy that clause.
The baseline full-query match remains permissive, so incidental-word results
continue to qualify when the optional boost contributes nothing.

Third experiment: use a fixed, corpus-derived terminology lexicon, built without
test labels, to recognize multiword phrases inside queries. Explicit user quotes
take priority. Boost recognized phrases while retaining the full-query recall
path. Independently test a content-term minimum-match clause, preserving negation,
identifiers and qualifiers; do not hard-filter all natural-language queries by
every term. Before a full comparison, inspect actual `_analyze` and `_explain`
outputs for the failed and target passages. No `_explain` run was performed in
this analysis, so individual BM25 term contributions remain a hypothesis.

Evidence needed: boost activation rate, candidate/ranking changes, and lexical
and hybrid quality deltas on the entire development set. Stop a no-effect variant
before drawing conclusions from its timing differences. Preserve scope filters.

## 4. A generic reranker has not repaired these examples

The pinned MiniLM cross-encoder at depths20/50/100 keeps the double-hashing formula
outside top5 and favors AVL topic introductions over its invariant. Increasing
depth does not remedy missing context or prove better question understanding.
Measured median rerank overhead is 392/903/1532 ms. Defer deeper or larger models
until fusion and contextual retrieval are tested; do not promote the current
reranker based on unjudged outputs. The existing 360 new pairs remain pending.

## 5. Some queries are underspecified across a whole library

lq500_0009 asks why a strategy on a line needs care on a two-dimensional grid,
without naming peak finding. Returned geometry, line-labeling and classifier
passages are plausible competing topics. The author's intended lecture is not
available to the user-facing search system. Record ambiguity explicitly; test
user-supplied topic/collection scope or clarification as a separate UX change.
Do not silently rewrite evaluation queries using their authoring-video metadata.

## Execution and decision order

1. Replay k60/k10/k0 on the saved development candidates. It is cheap, isolates
   the proven fusion mechanism, and cannot repair passages absent from the pool.
2. Assess new top5 passages; report gains and losses together on identical queries.
   Preserve accepted labels and document analyst disagreements separately.
3. Run contextual-window and lexical-term experiments separately, then combine
   only individually justified changes. New text requires new judgments.
4. Keep query type breakdowns, pooled candidate coverage lower/upper bounds,
   Hit@1/5, MRR@5, end-to-end latency and index size. Unknown is never zero.
5. Freeze a candidate before fresh confirmation. Existing release thresholds are
   unchanged; this proposal does not authorize relaxing them or claim a pass.

Latency track remains independent: a network-prohibited lifecycle benchmark found
~205 ms median Qdrant construction overhead. Test bounded client reuse with deadline,
concurrency and ranking-equivalence checks. That is a latency fix, not a quality
explanation. No HNSW change or model replacement is justified by this audit yet.

Primary artifacts: `benchmarks/library_queries_500/before_after500_per_query.json`,
`tmp/search-quality-v2/run-01/{results,corpus}.json`,
`tmp/search-quality-v2/rerank-02/results.json`, and
`tmp/search-quality-v2/accepted-comparison-01/`.
