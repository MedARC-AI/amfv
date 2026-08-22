AMFV Web
========

FastAPI and React application for AMFV eval-set creation and review.

Current baseline
----------------

- `backend/`: FastAPI, SQLModel, Alembic, SQLite.
- `frontend/`: Vite, React, TypeScript, TanStack Router/Query, Bun tooling.
- `docs/ingest-format.md`: versioned producer-to-document JSONL contract.
- `docs/frontend-architecture.md`: React/API ownership, auth notes, and text-span contract.
- `docs/operations.md`: launch, tooling, generated-artifact, and deferred deployment ownership.

Setup
-----

1. Copy `.env.example` to `.env` and set real secrets.
2. From the monorepo root, install Python workspace dependencies with `uv sync --dev`.
3. Apply migrations with `cd web/backend && uv run alembic upgrade head`.
4. Install frontend dependencies with `cd web/frontend && bun install --frozen-lockfile`.

Local run commands
------------------

Start the backend and frontend in separate terminals:

```bash
bash ./scripts/backend.sh
bash ./scripts/frontend.sh
```

For backend development with FastAPI reload mode, use:

```bash
bash ./scripts/backend-dev.sh
```

For frontend development with Vite dev mode, use:

```bash
bash ./scripts/frontend-dev.sh
```

Use `bash ./scripts/dev.sh` if you want one terminal to start both.

The backend script creates `.env` from `.env.example` if needed, runs migrations,
seeds the first superuser, and starts the API on `http://localhost:21783`. The
frontend script builds the frontend and serves `dist` on `http://localhost:24861`.
The dev frontend script starts Vite dev mode on the same port. Both proxy
same-origin `/api` requests to the backend.

To expose the local app through Cloudflare Tunnel, see
`docs/cloudflare-tunnel.md`. The tunnel flow keeps browser API calls on
same-origin `/api` and uses `VITE_PUBLIC_APP_URL`/`PUBLIC_APP_URL` for copied
invite links.

For the Docker tunnel/deployment path, rebuild and restart with:

```bash
bash ./scripts/docker-redeploy.sh
```

For Docker development overrides, opt in explicitly:

```bash
docker compose -f compose.yml -f compose.dev.yml watch
```

The default SQLite database lives at:

```text
data/app.db
```

Default local login from `.env.example`:

```text
admin@example.com
changethis
```

Manual run commands
-------------------

- Backend API from the monorepo root: `cd web/backend && uv run fastapi dev app/main.py`
- Frontend from the monorepo root: `cd web/frontend && bun run dev`

Useful checks
-------------

- `uv run ruff check web/backend`
- `uv run ruff format --check web/backend`
- `uv run ty check web/backend/app`
- `uv run pytest web/backend/tests`
- `cd web/backend && uv run alembic upgrade head`
- `bash web/scripts/generate-client.sh`
- `cd web && bun run email:check`
- `cd web/frontend && bun run check`
- `cd web/frontend && bun run test:unit`
- `cd web/frontend && bun run build`
- `cd web/frontend && bun run test:e2e`

Use targeted checks while developing a focused slice: run the checks for the
layer you touched plus `frontend`'s generated-client smoke check when API/client
contracts are involved. Use the full backend test suite and full Playwright
harness at integration points and before final handoff.

Current product shape
---------------------

The app is React-first. End users enter through Review and Create, with Retrieval and Fact Decomposition under each. Admin/data users get dataset, document, moderation, task, paginated export, and metrics APIs under `/api/v1/admin`. Basic user management still uses the template `/api/v1/users` surface.

The interactive product currently includes:

- source-document-backed retrieval and fact-decomposition creation;
- retrieval, relevance, and fact-decomposition review workflows;
- home/my-work summary counts and next-task recommendation;
- admin datasets, generic JSONL document import, documents, moderation, task generation, bounded export, paginated user metrics, agreement, and inter-user agreement.

The AMFV-specific `/api/v1/admin/users*`, user-review-history, and legacy item-ingest endpoints are still intentionally deferred and should not be presented as working surfaces. Document import is the separate, working `/api/v1/admin/documents/import` contract described in `docs/ingest-format.md`.

Signup is invite-gated. OAuth is postponed. SQLite is the current database target, with Alembic migrations rather than `create_all`.

Development note
----------------

Bun is the frontend package manager. Frontend unit tests and the disposable-real-backend Playwright suite are separate commands. Run `bash ./scripts/generate-client.sh` after backend API contract changes and commit `frontend/openapi.json` plus regenerated `frontend/src/client/*`. MJML sources under `backend/app/email-templates/src` own the tracked HTML in `build`; `bun run email:check` enforces drift.
