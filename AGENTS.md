# Repository Guidelines

## Project Structure & Module Organization

- `backend/` is the entire Python backend, self-contained like `frontend/`: `app/`, `alembic/`, `alembic.ini`, `tests/`, `benchmarks/`, and `requirements*.txt` all live here. Run every command in this section from inside `backend/` unless noted otherwise.
- `backend/app/api/` contains FastAPI routes; `backend/app/services/` implements processing, search, authentication, and S3 storage.
- `backend/app/db/` holds SQLAlchemy models and repositories; `backend/alembic/` holds schema migrations.
- `backend/app/workers/` contains Celery tasks, the durable-job dispatcher, and maintenance processes.
- `frontend/src/` contains React/TypeScript components, API helpers, and styles. Docker builds frontend assets into `backend/app/frontend/dist/`.
- `backend/tests/` contains automated tests (`test_*.py`) plus `backend/tests/smoke/` chaos/smoke harnesses and `backend/tests/browser/` Playwright-style scripts; `backend/benchmarks/` contains evaluation scripts and reports.
- `docs/` documents architecture (`docs/architecture/`), search (`docs/search/`), Kafka analytics (`docs/kafka/`), and operations (`docs/operations/`); `ops/` contains monitoring and backup configuration, with experimental compose stacks under `ops/compose/` (run from the repo root, not `backend/`).

## Build, Test, and Development Commands

Use Python 3.11 and Node.js 22, matching CI. From `backend/`, install backend dependencies with `python -m pip install -r requirements.txt`.

- `docker compose up --build -d` (repo root): build and start the application stack after configuring `.env` from `.env.example`. Review the AWS credential mounts in `docker-compose.override.yml` for your machine.
- `cd frontend && npm ci`: install locked frontend dependencies.
- `cd frontend && npm run dev`: start Vite on port 5173; the backend must run separately.
- `cd frontend && npm run build`: type-check TypeScript and build production assets.
- `cd backend && python -m unittest discover -s tests`: run backend tests.
- `cd backend && python -m compileall app alembic`: check Python compilation.
- `cd backend && alembic upgrade head`: apply database migrations.

## Coding Style & Naming Conventions

Follow surrounding code: four-space indentation and `snake_case` for Python functions/modules; two-space indentation and `camelCase` for TypeScript functions. Use `PascalCase` for classes and React components. Keep HTTP concerns in routes and reusable logic in services/repositories. No dedicated formatter or linter is configured; preserve existing style.

## Testing Guidelines

Tests use `unittest` and `unittest.mock`; name files `test_*.py` and methods `test_*`. Add focused regression tests for behavioral changes. Integration tests require isolated services through `VINDEX_TEST_DATABASE_URL`, `VINDEX_TEST_REDIS_URL`, or `VINDEX_TEST_ELASTICSEARCH_URL`; some skip when unset. No numeric coverage threshold is configured. Run relevant tests and the frontend build for UI changes.

## Commit & Pull Request Guidelines

History uses short imperative subjects, such as “Implement chunked search workers.” Keep commits focused. PRs should explain the problem, resulting behavior, validation, and migration/configuration changes. Link related issues when applicable and include screenshots for visible UI changes.

## Architecture & Configuration Safeguards

Postgres owns durable job state; Redis transports Celery messages. Preserve lease/token checks and attempt-specific artifact publication. Keep schema changes in Alembic migrations. Never commit `.env`, AWS credentials, or tokens. Library search and search projections require explicit feature configuration.

## Kafka Background Work and Follow-up

- Evaluate Kafka readiness against `docs/kafka/kafka_search_analytics_plan.md`; report each acceptance gate as passed, failed, or unverified using completed run artifacts. Admission rate is not consumer throughput, and accepted-event accounting is not zero capture loss.
- When starting or discovering an active background Kafka benchmark, record its process/session identity, output directory, start time, estimated completion time, and remaining acceptance gates in `docs/kafka/kafka_implementation_handoff.md`.
- Use the Codex automation tool to schedule a follow-up in the current task at the estimated completion time. Inspect existing automations first and update a matching monitor instead of creating duplicates. A note in this file does not itself schedule a wake-up.
- At the scheduled check, inspect the process and completed report. If the run is still active and incomplete, schedule the next check one minute later. If the scheduler cannot support that timing, state the limitation and use its supported timing; do not claim a wake-up was scheduled without tool confirmation.
- Stay quiet on unchanged, non-actionable checks. Notify the user on completion, failure, a meaningful change, or required user action. Stop the run's monitor when the run finishes or fails; inspect failures rather than polling an exited process indefinitely.
- If no background benchmark is running, say so explicitly. Do not create a polling monitor for nonexistent work or describe planned work as running. Continue separately authorized implementation work as appropriate.
- Keep tests isolated from production and preserve failed benchmark evidence. Do not lower acceptance gates merely to obtain passing results.

## Local AI Code Review

- `ops/review.ps1` runs the independent local reviewer; setup and the six course practices are documented in `tools/code_review/README.md`.
- Supply task intent and acceptance criteria with `--task` or `--task-file`. Use `--path` for bounded reviews; never interpret a partial or incomplete review as approval.
- The reviewer uses local embeddings/Chroma and ChatGPT-authenticated Codex. Do not add API-key fallback or automatic credit purchases. Account-level credit settings still apply.
- Preserve source/line evidence and risk prioritization. Developer feedback can propose reusable rules, but only reviewed changes to `tools/code_review/rules.json` activate them.
- Reviewer tests run with `tools/code_review/.venv/Scripts/python.exe -m unittest discover -s tools/code_review/tests -v`; they do not access application services or call models.
