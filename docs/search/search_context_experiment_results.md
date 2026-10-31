# Hybrid search: measured improvement investigation

2026-09-17. Serving configuration and AGENTS.md are unchanged.

## Decision

There is direct passage-level evidence that **retrieving a short child and supplying
bounded neighboring context repairs incomplete answers**. There is not yet reliable
evidence that a new fusion formula improves overall hybrid ranking. Do not report a
new accuracy number or promote a ranking change from this investigation.

The investigation found a second, material defect: the accepted automated labels
sometimes reward the right vocabulary in the wrong explanation. Keep the accepted
baseline as version1, but do not optimize a replacement toward these errors.
The new review is explicitly an AI draft, not human ground truth.

## Experiments actually completed

The development denominator is135 answer-seeking queries;15 candidate negatives
are excluded from positive-query metrics. Each query is counted once.

| Ranking | Hit@1 | Hit@5 | MRR@5 | Decision |
|---|---:|---:|---:|---|
| Existing RRF k60 |25.19%|65.19%|.3864|Retain control|
| RRF k10 |26.67%|64.44%|.3923|No general improvement;7 rescues,8 losses|
| RRF k0 |23.70%|56.30%|.3575|Reject|
| Equal-weight minmax score fusion |24.44%|64.44–65.19%|.3788–.3802|Reject as improvement|

The final row has missing-label bounds, not confidence intervals. Even its optimistic
bounds cannot beat the control on these metrics. Three new top5 pairs remain unjudged;
they cannot overturn this decision. No weight search followed this result.

Minmax combines score distances that RRF discards:
`0.5*(semantic-min)/(max-min) + 0.5*(lexical-min)/(max-min)`.
An absent branch contributes zero; a flat nonempty branch contributes0.5. This is
an offline cross-engine implementation, not a migration into Elasticsearch's native
retriever. See the [Elastic normalization reference](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/linear-retriever).

## Context experiment and its limits

Before execution, froze60s/120s caps, unchanged rankings, complete original anchors,
closest adjacent children, same video/revision, no query-specific rules. This tests
answer presentation independently of indexing. It does **not** test the planned
overlapping-parent reindexing experiment.

On development hybrid results,60s yielded no extra accepted-answer containment;
120s changed88/135 to92/135. Applied the same120s rule to all500 saved queries,
including the already exposed reserved split:

| Method | Original accepted-answer containment | With120s context | Added |
|---|---:|---:|---:|
| Qdrant semantic |264/450 (58.67%)|283/450 (62.89%)|19|
| Elasticsearch lexical |228/450 (50.67%)|248/450 (55.11%)|20|
| Hybrid |272/450 (60.44%)|289/450 (64.22%)|17|

These are **exact containment diagnostics, not newly judged Hit@5**. No original
answer text is removed by construction. That does not prove that longer results
are equally useful or that the inherited labels are correct. The reserved split
is exposed regression evidence, not fresh confirmation.

## What the actual text review found

Inspected all235 original top5 passages for47 development misses and recorded a
query-level diagnosis. Then reviewed the containment-bearing before/after pairs
for all17 apparent hybrid gains. Of those17:

- 5 have a supported incomplete-to-complete repair in this AI assessment.
- 11 remain incomplete or answer a different question.
- 1 original already answers the query under the review rubric.

The five supported pair repairs are:

| Query | Missing from original | Supplied by context |
|---|---|---|
|0017: angle between document vectors |Why normalization is needed|Different document lengths distort raw similarity; normalize for scale|
|0025: split, sort and combine a list |Full recursive split/merge relationship|Split L/R, recursively split, merge upward into a sorted array|
|0092: double-hashing probe sequence |The actual formula|`h(k,i)=(h1(k)+i*h2(k)) mod m`|
|0403: beam width |What the width constrains|Keep only the best fixed number of paths at every level|
|0404: search until backtracking |The worked dead-end example|Return to the last choice and follow another branch|

These are paired case findings, not five certified query-level Hit@5 gains: other
top5 passages have not all been independently regraded. The reviewer saw the
before/after relationship; this was not blinded confirmation.

Counterexamples matter:0172 retrieves amortized-analysis potential functions for
A*;0266 retrieves polynomial evaluation for interpolation;0274 retrieves hash
membership for ordered lookup;0395 retrieves induction for backward chaining.
More context does not correct those wrong-task matches.

For a control, selected20 development successes with seed20260917 and inspected
their first accepted grade2 result and its expanded version. The AI draft assessed
only6 original passages as complete answers and7 expanded passages as complete.
This is a warning about selected passage labels, **not** an estimate of query-level
error: another top5 result could still answer the query. All original labels remain.

## Cost and correctness checks

- All2500 hybrid anchors across500 queries preserve their exact original text,
 video, revision and playback anchor; windows stay within120s.
- Context multiplies result word count by4.35 on average (mean per-result ratio).
 Windows overlap within the top5 for250/500 queries. Automatically displaying all
 of that text is not justified by this experiment.
- Starting playback at the context start would move it earlier by42.38s on average,
 up to116.10s. Preserve the original child playback anchor and expose the wider
 interval separately.
- In-memory packaging alone measured0.042ms median and0.060ms p95 over1500 warmed
 operations. This excludes authorization, remote context fetching, serialization,
 network and rendering. It is not an end-to-end latency improvement claim.
- 18 focused experiment/scoring tests passed on local Python3.12.7. CI targets3.11;
 no new live-service or deployment test was performed in this continuation.

## Engineering plan selected by this evidence

1. **Repair the measurement before selecting another ranker.** Freeze a version2
   intent rubric: requested operation/direction, required fact or mechanism, and
   evidence span. Grade topic mentions as partial; wrong operations as irrelevant.
   Review a deduplicated, engine-blinded pool for both control and candidate. Keep
   version1 and version2 reports side by side. Unjudged pairs remain unknown.
   The37 reviewed gain/control pairs are calibration material, not confirmation.
   Reconcile disagreements against the original human-reviewed cases. Report AI
   assessment as such until independently verified; never rename it human review.
2. **Implement child-plus-context as the first product candidate.** Keep the
   current30s retrieval identity and playback anchor; offer a separate expandable
   context region capped at120s. Fetch only the accepted video/revision/attempt,
   with final authorization revalidation, deletion handling and the existing
   deadline. Failure to fetch context must not replace a valid anchor with stale
   text. Highlight the matched child. Measure whether users find the answer faster;
   do not score invisible expanded text as if it were the default snippet.
3. **Then test context in retrieval, separately.** Use one canonical parent
   manifest in ES and Qdrant,60s/120s windows at half stride, fixed embedding
   revision and unchanged fusion. Record truncation at the embedding model's token
   limit; longer text is not useful if the answer is truncated. Preserve child
   playback mapping. This requires fresh labels because all result text changes.
4. **Only revisit ranking when candidate coverage supports it.** Compare the
   exact requested mechanism with introductions in candidate traces. For phrases,
   inspect ES analysis/explanations before choosing a boost. Keep the existing
   reranker off the serving path until its quality gain justifies its measured
   CPU cost. Do not continue an unbounded fusion-parameter search.
5. **Promote only after frozen confirmation.** Preserve the existing quality gates;
   report Hit@1/Hit@5/MRR@5, query-type slices, paired and lecture-cluster uncertainty,
   regressions, duplicate results and full API p50/p95 under concurrency1/4. Add a
   fresh independently assessed confirmation set. Keep timeout/failure/deletion
   tests and a feature-flag rollback. No current experiment passes this release gate.

## Reproducibility

Runners: `benchmarks/context_containment_experiment.py`,
`benchmarks/context_full500_replay.py`, `benchmarks/linear_fusion_experiment.py`.
They use exclusive output directories and write their plans before computing
outcomes. Review notes are versionable text in
`benchmarks/development_failure_review_notes.txt`,
`benchmarks/context_gain_review_notes.txt`, and
`benchmarks/context_control_review_notes.txt`.

Artifacts under `tmp/search-quality-v2/`:

- `development_failure_review.json`:47 query diagnoses with exact original text.
- `linear-fusion-01/`:fixed plan, rankings, bounds and pending labels.
- `context-containment-01/`:all150 queries ×3 methods ×3 context sizes.
- `context-full500-01/`:all500 ×3 methods,17-gain paired review,20 seeded controls,
 and packaging cost/invariant checks.

This investigation supports the context-repair mechanism. It does not support
claiming an overall hybrid accuracy win, production latency improvement, or new
human-labeled benchmark result.

