# Child-plus-context quality pilot

The proposed strategy passed an exploratory paired test: some incomplete search
snippets became complete answers when the user opened neighboring transcript text.
This is a presentation improvement with unchanged hybrid rankings, not evidence
that Elasticsearch or Qdrant retrieval itself improved.

## Results

Ten selected development queries, all five results per query, both conditions;
98 distinct passages independently assessed against the same frozen rubric.
These are **AI-draft relevance judgments**, not human-confirmed benchmark scores.

| Metric | Original passage | Opened context, up to120s |
|---|---:|---:|
| Hit@1 |2/10 (20%)|5/10 (50%)|
| Hit@5 |5/10 (50%)|7/10 (70%)|
| MRR@5 |0.303|0.583|

Two queries gained a top-five answer; none lost one:

- `lq500_0036`: repeated maximum removal for sorting. The expanded third result
  supplies the worked heap example, reheapification and next extraction.
- `lq500_0374`: intelligent machine behavior. The expanded second result finishes
  the example of interpreting a video to answer questions about giving/taking.

Existing successes also became easier to find: binary insertion-sort costs and
RAM operations became complete in the first displayed result after expansion.
That can improve MRR without changing the rank order or discovering a new source.

Three queries still lack a complete top-five answer: amortized versus probabilistic
guarantees, cryptographic versus dictionary hashing, and radix-sort stability.
Their returned text discusses the wrong distinction or omits the requested reason.
Adding nearby text did not solve those failures.

## Evaluation safeguards and limits

- Selected three lexical, three paraphrase and four conceptual queries with
  seed20260918 before reading this pilot's passages. Plans and input hashes were
  saved before assessment. Rankings, fusion and context policy stayed fixed.
- Excluded earlier reviewed queries, including all47 previously labeled
  development misses. Consequently this pilot is drawn from an existing-success
  pool under version1 labels; it is **not representative of the full benchmark**.
  It is a mechanism test on different reviewed cases, not fresh confirmation.
- Hid method, rank, query ID, paired mapping and existing grade during review.
  Passage length can reveal context condition. One assistant assessed all items;
  this is neither independent human judgment nor a fully blind controlled study.
- Every positive grade includes an exact evidence excerpt. Both conditions were
  newly graded; no old grade was copied onto longer text. Original labels remain
  unchanged. On these selected queries, old version1 Hit@5 was100%, whereas the
  new original-text assessment is50%; that is a label disagreement, not a change
  to metric definitions or a retrieval regression.
- Paired-query percentile bootstrap95% intervals for the differences:
  Hit@1 [0,+60] percentage points; Hit@5 [0,+50] points; MRR [+0.100,+0.477].
  Authoring-lecture resampling covers eight clusters; its Hit@5 interval also
  includes zero. These conditional intervals do not account for judge error or
  review-selection bias. The pilot does not satisfy the release gate.
- The expanded condition assumes the text is actually opened and available to
  the user. The default collapsed snippet retains the original quality. These
  numbers cannot be advertised as improved default search accuracy.

## Cost and engineering decision

Mean result length rose from67.4 to262.4 words, a3.89-fold increase in total words.
Six of ten queries have overlapping expanded windows. Preserve the short result
and original playback anchor, with an explicit expandable context area; avoid
automatically flooding the result list with every expanded passage.

No new full API latency measurement was performed: this test replays saved
results. Earlier in-memory packaging timings exclude remote fetching and access
validation and remain unsuitable for a production latency claim.

Decision: retain120s as the candidate context cap for the next implementation
test; do not replace fusion or enable a serving default from this pilot. Next,
test the opt-in context path with accepted-revision/ownership/deletion checks and
deadline fallback, then assess a larger balanced pool including prior failures,
successes and negative candidates. Validate user effort as well as answer coverage.
Any larger quality report must retain separate original/context judgments and
make its assessment provenance explicit.

## Reproduce and inspect

All artifacts are under `tmp/search-quality-v2/context-quality-pilot-01/`:

- `review.html`: side-by-side preview of all ten queries and their five results;
  original text is highlighted within context; AI assessments are expandable.
- `plan.json`, `review_pool.json`, `mapping.json`: frozen selection and masking.
- `assessments.json`, `scores.json`, `summary.json`: all98 cited assessments,
  per-query grade vectors, category results and uncertainty estimates.
- `accepted_v1_comparison.json`, `cost.json`: preserved-label comparison and costs.

Runners: `benchmarks/context_quality_pilot.py`,
`benchmarks/score_context_quality_pilot.py`,
`benchmarks/build_context_pilot_report.py`. The builder uses an exclusive output
directory; scoring and report generation can be repeated from the saved pool.
Versioned reviewer notes: `benchmarks/context_pilot_review_01.txt`.

Validation:23 focused tests passed, including rejection of missing/duplicate
judgments, invented evidence and invalid grades. Local Python3.12.7; CI targets3.11.
Serving code, feature flags, original labels and AGENTS.md were not changed.
