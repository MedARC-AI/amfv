# Repository Guidelines

## Project Structure & Module Organization

`backend/` is a FastAPI, SQLModel, Alembic, and SQLite app. Routes live in `backend/app/api/routes/`, domain services in `backend/app/services/`, models in `backend/app/models.py`, schemas in `backend/app/schemas.py`, and migrations in `backend/app/alembic/versions/`. Tests are under `backend/tests/`.

`frontend/` is a Vite React TypeScript app using TanStack Router/Query, Tailwind, local UI components, and Playwright. UI code lives in `frontend/src/components/`, routes in `frontend/src/routes/`, hooks in `frontend/src/hooks/`, shared utilities in `frontend/src/`, and E2E tests in `frontend/tests/`.

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
- `bash ./scripts/frontend.sh`: build and preview the frontend on `http://localhost:24861` by default.
- `uv sync --dev` from the monorepo root: install all Python workspace packages and development tools.
- `uv run pytest web/backend/tests` from the monorepo root: run the web backend tests.
- `cd web/backend && uv run alembic upgrade head`: apply web database migrations.
- `cd web/backend && uv run alembic revision --autogenerate -m "describe change"`: create a model migration.
- `cd frontend && bun install --frozen-lockfile`: install frontend dependencies.
- `cd frontend && bun run dev`: run the frontend manually.
- `cd frontend && bun run test:unit`: run pure frontend tests.
- `cd frontend && bun run test:e2e`: run the serial, disposable-real-backend Playwright suite.
- `cd web && bun run email:check`: verify MJML-built HTML has no drift.

## Coding Style & Naming Conventions

Python targets 3.13. Use Ruff, Ruff format, and `ty`; keep routes thin and reusable behavior in services. Frontend uses Biome with spaces and double quotes. Components use PascalCase, hooks use `useCamelCase`, and Playwright tests use descriptive `*.spec.ts` names.

This repo is derived from `full-stack-fastapi-template`; inherited auth/user patterns are expected. User roles are `user`, `data_admin`, and `admin`. Signup is invite-gated. Local defaults from `.env.example` include `admin@example.com` / `changethis`.

## Testing Guidelines

From the monorepo root, run backend checks with `uv run ruff format --check web/backend`, `uv run ruff check web/backend`, `uv run ty check web/backend/app`, and `uv run pytest web/backend/tests`. Run frontend checks from `web/frontend` with `bun run check`, `bun run test:unit`, and `bun run build`. Use `bun run test:e2e` only for the explicit real-backend browser suite.

For full hook parity, install/run `prek` from `backend/`: `uv run prek install -f` and `uv run prek run --all-files`.

## Commit & Pull Request Guidelines

Recent commits use concise imperative subjects. Keep commits focused. PRs should include a summary, validation commands, linked issues when applicable, screenshots for UI changes, and explicit notes for migrations, generated client updates, ingest/export changes, or dataset-scoping implications.

## Security & Configuration Tips

Copy `.env.example` to `.env` and set real secrets through environment variables. Do not add production secrets or secret defaults to code. Preserve invite checks, role dependencies, and admin/user boundaries when changing auth-sensitive flows.
