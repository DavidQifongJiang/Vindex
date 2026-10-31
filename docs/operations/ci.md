# Continuous integration

`.github/workflows/ci.yml` runs on PRs to `main`, pushes to `main`, manual dispatch, and weekly. `ci-required` is the only check branch protection needs: it succeeds only when every other job succeeded, so a skipped or cancelled job cannot look green.

| Job | Proves |
| --- | --- |
| `backend` | Compilation, tests on real Postgres and Redis, migrations, and a real backup and restore with mocked S3. |
| `frontend` | Type-check and build, plus all browser suites on Playwright Chromium against production builds. |
| `container` | Compose config, the real image starts and migrates a disposable database, serves the built frontend, returns a row seeded in that database, and imports worker modules. |
| `kafka` | Publishing, consumption, crash recovery, replay, and rebalance on real Kafka and Postgres. |
| `elasticsearch` | Indexing, retrieval, isolation, replay, and deletion on real Elasticsearch and Postgres. |
| `review-tooling` | The local reviewer's offline tests. No model is called. |
| `dependency-audit` | Known advisories in all Python manifests and the npm lockfile. |

## Reproducing locally

Use disposable services, never the running application's. From `backend/`:

```
VINDEX_TEST_DATABASE_URL=... VINDEX_TEST_REDIS_URL=... python -m tests.run_ci --job backend
VINDEX_TEST_DATABASE_URL=... VINDEX_TEST_KAFKA_BOOTSTRAP=... python -m tests.run_ci --job kafka
VINDEX_TEST_DATABASE_URL=... VINDEX_TEST_ELASTICSEARCH_URL=... python -m tests.run_ci --job elasticsearch
```

From the repository root, after `npm ci` and `npx playwright install chromium` in `frontend/`, run `bash ops/ci/run_browser_suites.sh`. Set `VINDEX_BROWSER_CHANNEL=msedge` to use an installed browser instead.

## Skipped tests

`tests/run_ci.py` fails a job when no tests are discovered, a test fails, a skip has no owner, a skip reason changes, or a test did not pass in the job that owns it.

A test that needs a service only some jobs provide goes in `backend/tests/ci_test_owners.json` with its owning job and exact skip reason. List tests by identity; there is no allowed skip count.

## Dependency advisories

Advisories appear without any commit, so they must not block unrelated work:

- A PR fails only on findings in the manifests it changes. Other findings are warnings.
- The weekly run fails on any finding.
- A push or manual run only warns.
- If an audit tool itself fails, the job always fails, because nothing was checked.

To accept a finding, add it to `ops/ci/audit_exceptions.json` after review. An entry that has passed its expiry date stops applying:

```json
[{"id": "PYSEC-2026-0000", "reason": "Why this does not affect VIndex", "expires": "2027-01-31"}]
```

**Open backlog:** the current entries were added on 2026-10-31 so CI could be validated. They are existing findings whose impact has not been assessed, not findings judged safe. They expire on 2026-11-30, after which the weekly run fails until the packages are upgraded or each finding is reviewed.

## What CI does not prove

- Browser suites use fixtures. They verify UI behavior, not real Cognito sign-in or cloud video processing.
- Real AWS storage, Cognito, Whisper, and full video processing need separately authorized release verification.
- Kafka correctness tests say nothing about throughput or the acceptance gates in `docs/kafka/kafka_search_analytics_plan.md`.
- CI makes no AI calls. The code reviewer remains an explicit step (`tools/code_review/README.md`).

Failures are not retried automatically, so an intermittent failure stays visible.

## Validation evidence

On 2026-10-31, each deliberate failure below turned `ci-required` red on a throwaway draft PR. Run numbers are GitHub Actions run IDs.

| Deliberate failure | Job that failed | Run |
| --- | --- | --- |
| Broken TypeScript | `frontend`, `container` | 41169335643 |
| Failing backend assertion | `backend` | 41169767423 |
| Elasticsearch URL unset | `elasticsearch` | 41170029921 |
| Kafka unreachable | `kafka` | 41212676566 |
| Skip with no owner | `backend` | 41213234205 |
| Browser suite not run | `frontend` | 41213520791 |
| Zero tests discovered | `backend` | 41213817497 |
| Vulnerable dependency added | `dependency-audit` | 41214135380 |
| Run cancelled by a newer push | Several cancelled, none failed | 41169303128 |

The cancellation row comes from a real superseded run, because the scripted attempt (41214469641) finished before its newer push arrived.

Five consecutive clean runs passed: 41214712012, 41215020823, 41215353779, 41215681758, and 41215992370. Five runs give some confidence against flaky tests, but cannot rule them out.
