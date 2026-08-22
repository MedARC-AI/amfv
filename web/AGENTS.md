# Repository Guidelines

## Project Structure & Module Organization

`backend/` is a FastAPI, SQLModel, Alembic, and SQLite app. Routes live in `backend/app/api/routes/`, domain services in `backend/app/services/`, models in `backend/app/models.py`, schemas in `backend/app/schemas.py`, and migrations in `backend/app/alembic/versions/`. Tests are under `backend/tests/`.

`frontend/` is a Vite React TypeScript app using TanStack Router/Query, Tailwind, local UI components, and Playwright. UI code lives in `frontend/src/components/`, routes in `frontend/src/routes/`, hooks in `frontend/src/hooks/`, utilities in `frontend/src/lib/`, and e2e tests in `frontend/tests/`.

Read `docs/frontend-architecture.md` before changing review/create flows or highlighting. Read `docs/ingest-format.md` before touching ingest/export contracts. Larger planned efforts are organized in `workflows/*/{slices,reviews,results}`.

## Domain Model & Glossary

AMFV is an eval-set creation and review workflow. The core content graph is `Dataset -> Document -> Chunk -> EvalItem -> EvalFact`. `Dataset.eval_type` is either `RETRIEVAL` or `FACT_DECOMP`; this value scopes most creation, review, export, and admin behavior.

Review-side nouns include `ReviewTask`, `Review`, `Assignment`, `ItemJudgment`, `RelevanceJudgment`, and `Adjudication`. Preserve `dataset_id` scoping for documents, items, tasks, reviews, exports, and stats.

## Critical Contracts

The backend is the source of truth for auth, roles, dataset/eval-type scoping, validation, moderation, task generation, agreement, metrics, and export shapes. The frontend should submit payloads, render returned flags, and refresh data after mutations; do not reimplement server business rules in React.

Text spans use Python code-point offsets, not browser UTF-16 offsets. React selection code must compute offsets with `Array.from(chunkText)`. The backend checks `chunk.text[start:end] == text` and rejects stale or cross-dataset spans.

Do not hand-edit generated files: `frontend/src/client/*`, `frontend/openapi.json`, or `frontend/src/routeTree.gen.ts`. After backend API changes run `bash ./scripts/generate-client.sh`, then `cd frontend && bun run check-client`.

## Build, Test, and Development Commands

- `bash ./scripts/dev.sh`: start the dev backend and frontend together.
- `bash ./scripts/backend.sh`: apply migrations, seed the first superuser, and serve the backend with `fastapi run`.
- `bash ./scripts/backend-dev.sh`: apply migrations, seed the first superuser, and serve the backend with `fastapi dev`.
- `bash ./scripts/frontend.sh`: start Vite on `http://localhost:5173`.
- `cd backend && uv sync`: install backend dependencies.
- `cd backend && uv run alembic upgrade head`: apply migrations.
- `cd backend && uv run alembic revision --autogenerate -m "describe change"`: create a model migration.
- `cd frontend && bun install`: install frontend dependencies.
- `cd frontend && bun run dev`: run the frontend manually.

## Coding Style & Naming Conventions

Python targets 3.10+. Use Ruff, Ruff format, and `ty`; keep routes thin and reusable behavior in services. Frontend uses Biome with spaces, double quotes, and semicolons as needed. Components use PascalCase, hooks use `useCamelCase`, and Playwright tests use descriptive `*.spec.ts` names.

This repo is derived from `full-stack-fastapi-template`; inherited auth/user patterns are expected. User roles are `user`, `data_admin`, and `admin`. Signup is invite-gated. Local defaults from `.env.example` include `admin@example.com` / `changethis`.

## Testing Guidelines

Run backend checks with `cd backend && uv run ruff check .`, `cd backend && uv run ty check app`, and `cd backend && uv run pytest`. Run frontend checks with `cd frontend && bunx tsc -p tsconfig.build.json`, `cd frontend && bunx biome check ./src ./tests`, `cd frontend && bun run build`, and `cd frontend && bun run test`.

For full hook parity, install/run `prek` from `backend/`: `uv run prek install -f` and `uv run prek run --all-files`.

## Commit & Pull Request Guidelines

Recent commits use concise imperative subjects such as `Improve NICE source document flow`. Keep commits focused. PRs should include a summary, validation commands, linked issues when applicable, screenshots for UI changes, and explicit notes for migrations, generated client updates, ingest/export changes, or dataset-scoping implications.

## Security & Configuration Tips

Copy `.env.example` to `.env` and set real secrets through environment variables. Do not add production secrets or secret defaults to code. Preserve invite checks, role dependencies, and admin/user boundaries when changing auth-sensitive flows.
