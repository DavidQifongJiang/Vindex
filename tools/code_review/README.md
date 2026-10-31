# VIndex local review agent

An independent, advisory reviewer following DeepLearning.AI/Qodo's **AI Code Review** course.
The first milestone runs locally before a PR: Git snapshots -> syntax-aware context ->
local embeddings/Chroma -> independent security and pattern reviews -> validated report.

## The six practices

| Course practice | Implementation |
| --- | --- |
| 1. Independent review harness | Separate security and pattern prompts; fresh ephemeral Codex sessions; no generation chat history. |
| 2. Review locally before opening a PR | Branch, staged, and worktree modes; local JSON and Markdown reports. |
| 3. Thorough task-level context | Live reviews require `--task` or `--task-file`: problem, intended behavior, acceptance criteria. Missing facts become questions. |
| 4. Repository rules and engineering standards | `rules.json`, baseline `AGENTS.md`, relevant architecture contracts, retrieved source and tests. Changes to repository rules are evidence, not new instructions. |
| 5. Prioritize risk; retain developer judgment | P1/P2/P3 findings include a scenario, impact, correction, test and verifiable source quote. Reviews are advisory; no auto-fix, merge or approval. |
| 6. Review, judge, capture and reuse | `feedback` records developer decisions. `propose-rule` creates a draft; the developer reviews it, updates `rules.json` and adds a regression case. |

Course sources:
- [Best practices, part 1](https://learn.deeplearning.ai/courses/ai-code-review/lesson/79yprh/ai-code-review-best-practices-%E2%80%93-part-1)
- [Best practices, part 2](https://learn.deeplearning.ai/courses/ai-code-review/lesson/jcm17p/ai-code-review-best-practices-%E2%80%93-part-2)
- [Context engine](https://learn.deeplearning.ai/courses/ai-code-review/lesson/53tjuk/building-a-context-engine)
- [Specialized reviewers](https://learn.deeplearning.ai/courses/ai-code-review/lesson/jcm17m/building-specialized-code-review-agents)

The course uses paid OpenAI embeddings/API calls. This implementation keeps its retrieval
and specialist structure but uses local ONNX `all-MiniLM-L6-v2` embeddings and the signed-in
Codex CLI. Model quality must be measured independently; course benchmark results do not
describe this agent.

## Setup and usage

Prerequisites: Git, uv, Codex CLI with `exec --ignore-user-config` support, and ChatGPT sign-in.
From the repository root in PowerShell:

```powershell
.\ops\setup_code_review.ps1
.\ops\review.ps1 doctor
```

Setup installs Python 3.11 and exact hashed dependencies in isolated, ignored directories.
The first context build downloads an approximately 80 MiB checksummed embedding model.
Subsequent embeddings run locally. There is no embedding server or API key to configure.

```powershell
# Prepare context without running either AI reviewer.
.\ops\review.ps1 prepare --base HEAD~1 --head HEAD --task "Describe the intended change and acceptance criteria."

# Review the branch against its merge base with origin/main.
.\ops\review.ps1 review --base origin/main --head HEAD --task "Describe the problem, intended behavior, and acceptance criteria."

# Review only staged changes against HEAD.
.\ops\review.ps1 review --mode staged --task "Retries must keep the same upload identity. A changed payload must return 409."

# Review tracked/untracked local source changes against HEAD, scoped to one area.
.\ops\review.ps1 review --mode worktree --path backend/app --task-file C:\path\to\requirements.txt
```

On other platforms create the isolated environment with Python 3.11, sync the lock file,
then run `python -m tools.code_review` from the root. The PowerShell wrapper is optional.

`--path` can be repeated. The reviewer refuses diffs over 30 files or 55,000 characters;
split a large PR into explicit scopes. It never silently drops the end of a diff.
Branch mode reads immutable Git objects; staged mode reads the Git index; worktree mode
includes eligible untracked source and records a content fingerprint. Files outside the
source/documentation allowlist, environment files, credential files, symlinks, storage,
media, caches, benchmark datasets and dependencies are not sent for review.

Reports and exact input context are written beneath `.review-cache/reports/<id>/`.
They contain source snippets, so keep this ignored directory local. A completed identical
review is reused; `--refresh` explicitly requests another run. Each live run makes at most
two top-level Codex calls, sequentially, with a per-reviewer timeout (default 180 seconds).

Exit code 0 means the requested operation completed, **not approval to merge**. Exit code 2
means it failed or the review is incomplete. Findings are advisory even when P1. A partial
run retains validated findings with `status: incomplete`. Check questions and limitations.

## Feedback and reusable rules

Use the full report path and finding ID printed in the report:

```powershell
.\ops\review.ps1 feedback --report C:\path\to\review.json --finding FINDING_ID --verdict accepted --reason "The retry can create duplicate jobs; confirmed with a regression test."
.\ops\review.ps1 propose-rule --feedback C:\path\to\feedback.jsonl --finding FINDING_ID --rule "Upload retries must preserve owner-scoped identity and create only one set of jobs."
```

Other verdicts are `false-positive`, `intentional`, and `needs-discussion`. Use the reason
to challenge the scenario, evidence or consequences. Rule proposals are **not active**.
Review the proposal, edit `rules.json`, and add a focused fixture before reusing it.
No feedback, rule proposal or report is posted to GitHub automatically.

## Cost and execution boundaries

- No OpenAI/Anthropic API integration or automatic paid fallback. API keys, cloud credentials
  and provider endpoint overrides are stripped from the child environment.
- ChatGPT sign-in is checked before each call and forced in the CLI config. API-key login
  is rejected. Exhaustion/errors stop the run; the harness does not buy credits or retry
  through another provider.
- **Account billing still applies.** Codex may use existing purchased credits according to
  your account settings. This harness cannot distinguish included allowance from purchased
  credits or enforce an account-wide zero-dollar limit. Disable any account-level automatic
  recharge/extra usage if you want that guarantee; no billing settings are changed here.
- Codex runs in an empty temporary directory, with user config/rules ignored, repository
  instruction discovery disabled, read-only sandboxing, and shell/apps/plugins/hooks/browser
  tools disabled. Source text is untrusted evidence. The harness does not execute PR code.
- Chroma is local with telemetry disabled. Only the bounded review packet goes to Codex.
  Local model downloads and package installation require Internet access, not inference fees.
- Cache/environment paths are excluded from Git and the application's Docker build context.
  This tool does not access VIndex's running services, AWS credentials or production data.

## Verification and current limits

```powershell
tools/code_review/.venv/Scripts/python.exe -m unittest discover -s tools/code_review/tests -v
```

Tests cover immutable/staged/worktree snapshots, context revision isolation, TypeScript/Python
chunking, forbidden paths, changed-line/evidence validation, authentication, failed calls,
and feedback-to-rule proposals. Tests never call a model or application services.

Retrieval is selective, not exhaustive. Documentation snippets are bounded and record their
truncation. Long lines may exceed the embedding model's token window. Exact unchanged file
moves are omitted, so path/import consequences of a pure move need separate developer review.
Modified moves are reviewed as additions/deletions. Paths outside the allowlist are excluded.
CI/test execution, GitHub publishing, conversational follow-up, and a labeled held-out quality
benchmark are later milestones. Test source is never represented as proof that tests passed.

API/CLI references: [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[Chroma local embeddings](https://docs.trychroma.com/docs/embeddings/embedding-functions).
