# Evidence-driven search decision project

Status: executable investigation plan, 2026-09-17. No production promotion.
Continuation results: `docs/search_context_experiment_results.md`. Query-level AI
diagnoses cover all47 development misses; paired context review covers17 apparent
gains and20 seeded success controls. This is not complete blinded regrading.
Equal-weight minmax fusion was also tested and rejected as an improvement.
Context packaging repairs specific answers, but inherited label conflicts prevent
claiming its containment increase as an overall accuracy gain. Repair measurement
before selecting a replacement ranker; keep the version1 labels unchanged.
AGENTS.md is unchanged. This plan turns the failure audit into a repeatable
detect → explain → test → accept/reject workflow. It supplements the existing
quality work plan; its release thresholds are not relaxed.

## Objective and scorecard

Improve complete-answer retrieval while preserving latency, access, revision and
deletion correctness. Grade2 means the displayed passage answers the request;
grade1 means substantive but incomplete, grade0 means no answer. Keep Hit@1,
Hit@5 and MRR@5 primary. Preserve the accepted baseline label snapshot and its
automated/human provenance. New AI assessments never become human judgments merely
because the reviewer is acting in a human-review role.

Existing evidence: full500 has 450 answer-seeking queries plus 50 candidate
negatives. Development has 135 plus 15. Do not mix these denominators or count
repeated requests as independent queries. The exposed reserved set is regression
evidence, not a fresh confirmation set.

## Work package A — deterministic failure detector (implemented)

Flag each query/method with:

1. Top1 failure: grade of rank1 is below2; unknown means unresolved.
2. Top5 failure: all returned top5 grades below2; any unknown without a known
   answer leaves the outcome unresolved.
3. Fusion loss: hybrid misses but an input branch's top5 contains grade2.
4. Candidate ranking loss: top5 misses but the union contains a known grade2.
5. Candidate coverage unresolved: no known grade2 in the union; this does not
   prove the answer is absent. Review/search source text before classifying it.
6. Regression: a candidate configuration loses an accepted baseline success.

`benchmarks/audit_search_failures.py` routes all178 full500 hybrid misses: 81 have
an accepted answer in a branch top5; 97 do not. The development union contains a
known answer for 24 of its47 hybrid misses; the other23 are unresolved.

Deliverable: per-query failure ledger with query type, source artifacts and hashes.
Completion means every failed query is routed, not that every root cause is known.

## Work package B — fusion decision trace (implemented and replayed)

For each candidate preserve semantic/lexical rank, accepted grade or unknown,
individual rank contributions, total fusion score, resulting rank, and margin
against the fifth-place score. Ties follow the existing passage-ID order.

Formula: S(d)=ws/(k+rs(d))+wl/(k+rl(d)); a missing rank contributes zero.
Use these values to explain a decision, never interpret S as answer probability
or apply a universal relevance cutoff. Scores depend on k, weights and overlap.

The current equal-weight k60/top50 gives a dual-rank50 document .01818, above
a single-engine rank1 document .01639. This establishes a strong overlap bias,
but does not establish that lowering k improves quality.

Frozen diagnostic replay: equal weights, same top50, k values60/10/0. Run:
`python -m benchmarks.trace_fusion_decisions` (exclusive output directory).
Artifacts: `tmp/search-quality-v2/fusion-audit-01/` with plan, full traces,
per-query scores, label bounds and pending labels.

| k | Hit@1 | Hit@5 | MRR@5 | Rescued/lost vs k60 |
|---|---:|---:|---:|---:|
| 60 | 25.19% | 65.19% | .3864 | baseline |
| 10 | 26.67% | 64.44% | .3923 | 7 / 8 |
| 0 | 23.70% | 56.30% | .3575 | 8 / 20 |

All135 positive-query primary metric values are determined. One newly promoted
pair is unjudged, but it occurs after a known answer and does not affect these
three metrics; 134 queries are fully passage-labeled across all three methods.
Do not claim that all passages are labeled or use this to compute graded nDCG.

Decision: reject k0 as a replacement. Do not promote k10: it trades first-rank
improvement for lower coverage and does not meet the existing promotion gate.
These are development observations, not statistically proven population effects.
Do not expand into an unbounded weight/grid search on the same labels.

## Work package C — structured passage review

Review the 47 development misses, plus every newly rescued/lost query and a
fixed seed20260917 sample of20 baseline successes as a control. Deduplicate pairs.
Create an engine/rank-blinded first view; reveal traces only after grading.
Keep all original labels. If review disagrees, record a separate proposed change
with rationale; report accepted-baseline and review-based sensitivity separately.

Each review record must contain:

- Query ID/text and an explicit description of the requested fact/mechanism.
- Exact passage identity, transcript revision, video/timestamps and displayed text.
- Proposed grade, quoted local evidence, missing information, confidence and
  provenance (`ai_review_draft` or separately verified human review).
- Root cause(s): wrong topic, missing requested relation, introductory mention,
  split context, transcription/notation problem, fusion demotion, candidate miss,
  query ambiguity, or label conflict. Multiple causes are allowed.
- Candidate ranks before/after, proposed repair, falsifying test and next action.

Initial analyst cases, based on actual source inspection:

| Query | Observation | Diagnosis and scope |
|---|---|---|
| 0092 Double hashing probe sequence | Term at2174.59s, formula at2197.27s. Formula semantic4 → fusion15; k10 gives13 and k0 gives9. | Split term/formula plus fusion demotion. Lower k alone fails this case. All tested reranker depths also miss the formula in top5. |
| 0051 AVL height-balance invariant | Exact at-most-one explanation at812.89s is semantic18/fusion40; topic introductions rank higher. | Answer specificity/context problem. A label-based k10 rescue does not demonstrate retrieval of this exact invariant. Review accepted grade2 introductory passages separately. |
| 0037 Why heap construction is linear | Argument distributed across2345–2525s; top result at3104s discusses heapsort n log n. | Right vocabulary, wrong requested reasoning, plus fragmented proof. Generic reranking also selects heapsort. |
| 0009 Line versus two-dimensional grid | Query omits peak finding; geometry/line-labeling passages returned. | Intended topic is underspecified. Do not insert authoring-lecture knowledge unavailable at serving time. |

These cases are worked examples, not a completed review of all47 misses. Complete
the ledger before estimating the prevalence of semantic root causes.

## Work package D — repair experiments selected by reviewed cause

Run one cause-specific experiment at a time with a preregistered configuration,
full development regression check and new top5 judgments. Never fix only the
showcase queries and claim a global improvement.

**D1: contextual retrieval, highest priority after the failed fusion replay.**
Evidence: term/formula and invariant separation. Compare unchanged children with
60s and120s overlapping parent windows, half-window stride, bounded by accepted
video/revision. Keep child playback anchors, use one shared parent manifest for
both engines, avoid generated summaries. These sizes are test values, not facts
about optimal chunking. Require new labels for changed text. Track complete-answer
coverage, timestamp usefulness, duplicate windows, index size and latency.
Reject a variant that merely returns longer irrelevant text. Earlier 0/8 context
expansion results prevent assuming success here.

**D2: actual technical-phrase matching.** Evidence: prior phrase boost matched
the full short query, not embedded "double hashing"; all four variants had
identical rankings. Extract bounded phrases from an independently built corpus
terminology list or explicit quotes; keep full-query recall path. Obtain `_analyze`
and `_explain` for wrong and target documents before choosing analyzer/query
changes. No such ES explanation has yet been run, so term-level BM25 attribution
remains unverified. Measure activation, rank change, rescued/lost queries and
all query-type scores. Avoid default all-term requirements or label-derived terms.

**D3: answer-specific ranking, conditional.** Only if reviewed candidate coverage
is sufficient but introductions still beat explanations. Reassess the existing
reranker on the new coherent passages at depth20 before considering larger models
or deeper pools. Current depths add392/903/1532ms median and fail the worked cases.
Do not deploy a confidence-based router using uncalibrated raw engine/RRF scores.

**D4: ambiguity/visual dependence.** Preserve unclear queries as a separate slice.
Test user-supplied topic scope/clarification as a product experiment, not a hidden
benchmark rewrite. Diagram-dependent passages may need visual context; do not
promise that index tuning can recover information absent from the transcript.

## Work package E — decision gates and stopping rules

- Scoring integrity: no silent unknown-to-zero conversion, no changed labels on
  unchanged text without an explicit versioned review, and no mixed query sets.
- Every report includes baseline/candidate counts, quality deltas, rescued/lost
  IDs, lexical/paraphrase/conceptual breakdowns, paired query and lecture-cluster
  uncertainty, latency and resource costs. Evaluate failures/timeouts explicitly.
- Retain the existing work-plan promotion gate: Hit@5 gain >=3pp over semantic
  with positive lower95% bounds for both paired-query and lecture-cluster
  resampling, plus its semantic-protection, category, absolute-quality and
  correctness requirements. Development success only selects a candidate.
- If an experiment does not change rankings, stop and investigate activation.
  If it rescues examples but loses more queries, reject it as a general fix.
  If unknowns can reverse the result, report BLOCKED rather than a winner.
- Freeze one candidate before fresh independently reviewed confirmation and
  deployment testing. A failed confirmation returns to development; no repeated
  tuning on the same confirmation set. No thresholds changed in this document.

## Separate latency workstream

Qdrant constructor cost ~205ms is measured with network prohibited. Bounded client
reuse is justified independently of relevance. Preserve per-request deadlines,
admission while work continues, shutdown and access/revision validation. Verify
identical rankings and paired API latency at concurrency1 and4 before claiming a
speedup. The lifecycle measurement is not itself an end-to-end optimization.

## Deliverables and completion definition

1. Failure ledger, per-candidate score traces and versioned review ledger.
2. Each experiment's fixed plan, reproducible results and explicit accept/reject.
3. One selected configuration or a documented no-improvement finding, with final
   validation status and rollback plan. Do not force a hybrid winner.

Immediate next action: complete the blinded version2 relevance pool and reconcile
the documented label disagreements. The47-miss query diagnosis and bounded context
packaging experiment are complete; parent reindexing is not. The detailed next
experiment and promotion requirements are in `search_context_experiment_results.md`.
The project is not marked complete and no ranking change is promoted.
