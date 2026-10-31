# Search quality implementation work plan

Date: 2026-09-17. Status: active; no release approval or quality pass implied.
This is the execution plan for the user's request to improve Elasticsearch and
Qdrant together. Existing unrelated working-tree changes must be preserved.

## Outcome and architecture

Find an answering passage across the user's videos and play at its timestamp.
Keep Qdrant for semantic retrieval and Elasticsearch for exact terms, names, and
phrases. Keyword extraction is a separate query-processing experiment, not a
replacement for Elasticsearch's retrieval role. Preserve the original query for
semantic retrieval; never remove negation or qualifiers from the semantic query.

Start with the existing shared passage snapshot and top 50 candidates per engine.
Capture the authorized, revision-validated candidate union. Diagnose missing
answers separately from ranking losses. Experiment with conservative lexical
query enrichment and offline cross-encoder reranking before changing serving
defaults. Coherent passage reconstruction and embedding replacement are
conditional follow-ups only when candidate coverage shows they are necessary.

## Fixed provisional acceptance thresholds

These are newly selected working targets, not historical approved metrics or
performance guarantees. Changing them requires explicit user permission.

| Gate | Requirement |
| --- | --- |
| Overall answer quality | Hit@5 >= 80%, Hit@1 >= 50%, MRR@5 >= 0.60; grade 2 answers only |
| Statistical floor | Overall Hit@5 95% Wilson lower bound >= 70%; also report lecture-cluster uncertainty |
| Query categories | Hit@5 >= 70% separately for lexical, paraphrase, conceptual queries |
| Candidate coverage diagnostic (nonblocking) | Target >= 90% of verified answerable queries with an answer in the union of both top-50 lists; not exhaustive recall or a release gate |
| Elasticsearch utility | Lexical-only Hit@5 >= 70% on verified lexical queries; report unique answers it contributes beyond Qdrant |
| Semantic protection | Candidate versus unchanged semantic baseline: paired 95% lower difference bound >= -0.02 for Hit@1 and MRR@5; report per-category regressions |
| Hybrid promotion | Hit@5 gain >= 3 percentage points over semantic and paired 95% lower difference bound > 0; otherwise semantic may remain default |
| Warm performance | Client-observed p95 <= 1,000 ms, p99 <= 2,000 ms on fixed deployment hardware, four search clients, active ingestion, >= 30 minutes and >= 1,000 requests |
| Failures | Unexpected errors/timeouts <= 1% of all healthy-load attempts; zero 429s at the four-client healthy load; deliberate overload is a separate drill |
| Correctness | All required access/revision/deletion/retry/failure/overload tests pass; zero observed unauthorized or unpublished results |
| Review | Independent code and evidence review; no unresolved critical/high-severity findings |

Report each mandatory gate as PASS, FAIL, or BLOCKED with artifact links. Missing evidence
is BLOCKED. Local caption/ASGI timing cannot pass the authenticated deployment
gate. Cold startup is reported separately with bounded rejection/recovery.
Reliable abstention is a separate milestone: >= 80% correct abstention on verified
negatives and <= 5% incorrect abstention on verified positives. Do not silently
include that new product capability in this release or claim it already exists.

## Evaluation contract

- Preserve existing reports. The latest prior comparison is
  `benchmarks/library_queries_500/before_after500_report.md`: real requests ran,
  but its automated judge failed positive calibration. Those relevance numbers
  are diagnostics only. Existing README and older scorecard status is historical.
- Use the exposed development queries for experiments; do not treat the exposed
  reserved set as blind. Freeze at most four lexical/fusion candidates and three
  reranking depths before executing comparisons. Select once on development.
- Final evaluation needs 150 new independently verified answerable queries, 50
  each lexical/paraphrase/conceptual, on accepted application transcripts. Keep
  50 verified negatives separate for the later abstention milestone.
- Freeze corpus, exact passage identities/text, model revisions, query/config
  hashes, and judgments. Preserve original query wording and answerability.
- Judge the pooled top-five results for every compared configuration. Expanded
  candidate coverage needs additional judgments: unknown never means irrelevant.
  Human judgments and automated suggestions remain separate. Independently
  review 20% of human judgments and resolve disagreements.
- Isolated capture runs create new revision UUIDs. Those UUIDs participate in
  deterministic tie-breaking, so rebuilding can change exact ties. Compare
  configurations within a run and replay its saved corpus/candidates for exact
  reproduction; do not claim cross-run identical rankings from text hashes alone.
- Count each query once for quality; repeated requests are latency samples.
  Report paired query bootstrap and lecture-cluster intervals. Existing selected
  difficult examples are useful diagnosis, not representative release scores.
- For paired promotion/noninferiority gates, BOTH query and lecture-cluster 95%
  percentile bootstrap lower bounds must pass. Use 10,000 resamples and seed
  20260917; cluster-resample whole source-lecture groups, retaining query-weighted
  macro means with variable group sizes. Report the fixed grouping limitation:
  source lecture is a dependence proxy, not the only possible relevant lecture.
- Healthy-load latency includes every attempted request, including errors and
  timeouts (record the timeout duration); never hide slow failures by reporting
  successful-request latency alone. Report successful-request latency separately.
- Never repeatedly tune against final evaluation until it passes. A failed final
  evaluation returns to development and requires fresh confirmation evidence.

## Execution stages and completion evidence

1. **Baseline and instrumentation.** Record working-tree state; run relevant
   regression tests. Add internal opt-in candidate capture without expanding
   public API limits or weakening scope validation. Store private review artifacts
   separately from logs; transcript/query text must not enter telemetry.
2. **Candidate diagnosis.** Run unchanged semantic, lexical, and equal-weight RRF
   on the same isolated corpus. Export a blinded review pool and explicit
   known/unknown candidate coverage bounds. Review false matches and missing
   explanations before claiming an engine is strong.
3. **Lexical experiments.** Compare current full-query BM25 with bounded phrase
   boosting and conservative optional query terms. Preserve a full-query recall
   path, avoid fuzzy/synonym/title boosts without evidence, and preserve all
   permission/revision filters. No schema migration is needed for query-only
   experiments. Evaluate lexical utility and overall regressions separately.
4. **Ranking experiment.** If the candidate pool contains answers, compare a
   pinned cross-encoder at depths 20/50/100 with unchanged RRF and semantic-only.
   At reduced depths preserve candidates from both engines. Do not let an RRF
   top-five truncation discard answers before reranking. Judge new candidates.
5. **Conditional retrieval repair.** If answers are absent from the union, test
   sentence-aware passages before model replacement. Rebuild both engines from
   one new manifest and regenerate embeddings; never reuse old labels/IDs for
   changed text. Any new experiment budget is documented before execution.
6. **Serving integration.** Integrate a demonstrated candidate behind an off-by-
   default flag. Allocate inference budget within the three-second API deadline;
   retain slots for unfinished inference; deterministic fallback; revalidate
   authority after expensive ranking. Verify recovery and deletion races.
7. **Confirmation and operations.** Run fresh verified relevance evaluation and
   authenticated deployment load tests. Stage a reversible activation only after
   required gates pass; deployment itself is a separate operational action.

## Independent review and persistence

The main agent implements; a separate agent reviews the actual code, tests, and
evidence after each consequential stage. Findings return to implementation and
are rechecked. Reviewer approval is not a substitute for relevance judgments.

Continue useful authorized work while gates are unresolved. If human judgments,
service access, or hardware are unavailable, record the precise blocker and
prepare the next reviewable artifact. Do not fabricate evidence, lower targets,
quietly substitute easier queries, or mark the goal complete. Ask permission
before threshold/scope changes, showing measured results and the proposed delta.

## Progress

- [x] Inspect current code and latest evidence; independent plan critique.
- [x] Freeze this provisional plan and identify label/deployment prerequisites.
- [x] Baseline checks and candidate instrumentation (59 relevant tests: 53 passed, 6 skipped in desktop progress check).
- [x] Candidate capture and blinded review packet (`tmp/search-quality-v2/run-01`, `tmp/search-quality-v2/diagnostics-01`).
- [ ] Complete candidate diagnosis; newly retrieved passages require labels.
- [x] Four lexical profiles and reranker depths 20/50/100 executed; results recorded. Reranker quality remains partially labeled.
- [ ] Independently reviewed serving integration, if supported by evidence.
- [ ] Fresh quality and deployment gates passed.

Current blockers: no trustworthy representative relevance set or untouched
confirmation queries are available yet. Existing 30 human judgments can guide
diagnosis but cannot certify these thresholds. Runtime/service availability is
being checked; no numerical gate has been relaxed.

## Continuation evidence — 2026-09-17

The user accepted the existing baseline labels ("The labels are correct. Now we
have the baseline") and requested continuing the CLI work and tests. Use those
labels as the working development baseline, retaining their automated origin and
the 30 original human overrides. This does not label newly retrieved passages or
create an untouched confirmation set. AGENTS.md must remain unchanged.

- CLI capture `tmp/search-quality-v2/run-01/results.json` completed 150 development
  queries × four lexical profiles × two rounds = 1,200 HTTP 200 responses, no
  degradation, complete coverage. All temporary indexes/schema cleaned up.
- `tmp/search-quality-v2/diagnostics-01/` contains candidate coverage bounds and a
  blinded review packet: 13,082 candidate pairs, including 30 original human labels.
- `benchmarks/score_accepted_candidates.py` reuses accepted labels only for exact
  query/video/start/end/text matches, with human labels taking precedence. Run:
  `python -m benchmarks.score_accepted_candidates --run tmp/search-quality-v2/run-01 --output NEW_OUTPUT_DIRECTORY`.
- `tmp/search-quality-v2/accepted-lexical-01/` scores all four profiles on the 135
  answer-seeking development queries; 15 candidate negatives remain separate.
  Every profile has identical candidate ordering and top-five quality. Phrase
  boosts change no candidate scores; term boosts change scores on six queries but
  no rankings. Do not interpret the timing differences as demonstrated improvement.
- Pinned cross-encoder runner: `benchmarks/rerank_candidates.py`, depths 20/50/100.
  The first download attempt was blocked by sandbox network access and left an
  empty `rerank-01` output directory. The authorized retry is `rerank-02`, with
  progress in `tmp/search-quality-v2/rerank-02.log`. Verify completed results before
  scoring; do not treat the existence of the directory or plan as completion.
- Runtime for this continuation: local Python 3.12.7, not the CI Python 3.11 target.
  Focused scorer/lexical/ranking/diagnostic tests: 21 passed. No production flags,
  ranking defaults, or AGENTS.md changes were made.

Completed continuation results:

- `tmp/search-quality-v2/rerank-02/results.json`: all 450 rerank evaluations
  completed (150 queries × depths 20/50/100). CPU rerank-only median/p95:
  depth20 392.4/543.5 ms; depth50 903.2/1207.9 ms; depth100 1531.8/2235.7 ms.
  These are not end-to-end deployment timings. Shared developer machine; brief
  scoring/test subprocesses ran during inference, so treat these as exploratory.
- `tmp/search-quality-v2/accepted-comparison-01/report.md` and `summary.json`:
  accepted-baseline scoring across all methods. Lexical profiles all tie:
  lexical Hit@1/Hit@5/MRR@5 = .1778/.5111/.2914; hybrid =
  .2519/.6519/.3864; semantic = .2667/.6000/.3935 on 135 development positives.
  No lexical-profile quality improvement demonstrated.
- `tmp/search-quality-v2/accepted-comparison-01/pending_top5.json`: 360 new
  query/passage pairs need assessment. Only 21 positive queries are fully labeled
  across every compared method; do not present that selected subset as overall
  reranker accuracy. Bounds are missing-label bounds, not confidence intervals.
- `benchmarks/profile_search_clients.py` and
  `tmp/search-quality-v2/client-lifecycle.json`: 20 measured lifecycle rounds per
  backend, after a warmup, with network sends prohibited. Qdrant constructor
  median/p95 = 204.8/222.0 ms; ES = .30/.68 ms. This isolates substantial Qdrant
  setup overhead; it does not yet measure an end-to-end pooling improvement.

Next actions: implement and test bounded client reuse with per-request deadlines,
then rerun paired latency/ranking checks against isolated services. Independently
assess the 360 new top-five pairs before choosing a reranker. No new labels have
been silently accepted, no quality threshold lowered, and no serving default
changed. Final confirmation and deployment load gates remain outstanding.

Final continuation verification: 72 tests executed, 66 passed and 6 integration
checks skipped because service environment variables were unset. Command:
`python -m unittest tests.test_accepted_candidates tests.test_candidate_diagnostics tests.test_lexical_query tests.test_search_ranking tests.test_library_search tests.test_hybrid_search tests.test_search_observability tests.test_primary_search_metrics -q`.
Output: `tmp/search-quality-v2/continuation-tests.log`. A 20-query blinded first
review batch is in `tmp/search-quality-v2/accepted-comparison-01/review_batch_01.md`.
This continuation did not start Docker services or a persistent model server;
reranking and lifecycle profiling processes finished. Earlier service cleanup
status must be checked separately before any future service operations.

Systematic decision plan (2026-09-17): `search_decision_project_plan.md`.
Fusion replay at `tmp/search-quality-v2/fusion-audit-01/` compared fixed k60/10/0
on the saved development candidates. Hit@5:65.19/64.44/56.30%; k10 rescues7 but
loses8, k0 rescues8 but loses20. Neither is promoted. New traces expose branch
contributions, fused ranks and fifth-place margins. One unknown pair does not
affect the primary metrics, but remains unjudged. Focused regression tests:
23 executed,22 passed,1 integration test skipped. Next: structured AI review
of47 development misses, prioritizing24 with known answers in the union, then
freeze the context-retrieval experiment. No serving or AGENTS.md changes.

Further continuation (2026-09-17): see `search_context_experiment_results.md`
for the current decision, superseding the immediate next action above. Completed
47-miss query-level AI diagnosis, a fixed equal-weight minmax fusion replay, and
60/120s context packaging (unchanged ranking). Minmax cannot outperform baseline
even at its optimistic missing-label bounds. Context120 increases accepted-answer
containment272→289/450 in the exposed full500 replay, but this is NOT new Hit@5.
Paired AI inspection of all17 apparent gains supports5 repairs;11 remain incomplete
or wrong and1 already answers the query. Twenty seeded success controls also show
material label disagreements. All reviews retain AI provenance and preserve the
accepted baseline labels. No full blinded relabel, parent reindex, production
context path or live-service latency test has been completed. Eighteen focused
tests passed;2500 anchor preservation/cap invariants checked. No serving defaults
or AGENTS.md changes. Next: freeze/reconcile a version2 evidence-based label pool,
then select and confirm a context candidate without reusing calibration as test.

Child-plus-context pilot completed: `search_context_quality_pilot.md`.
Fixed ten-query,98-passage method-masked AI review, all top5 results in both
conditions: Hit@1 .20→.50, Hit@5 .50→.70, MRR .3033→.5833. Two gained query answers,
zero losses. Context is the explicitly opened condition, not collapsed search.
Selected development pool excludes previous reviews and therefore prior accepted
misses; not representative confirmation. Hit@5 paired and lecture-bootstrap
intervals include zero. Original accepted labels preserved; new review is AI draft.
23 focused tests passed. Side-by-side preview at
`tmp/search-quality-v2/context-quality-pilot-01/review.html`. No serving changes.
