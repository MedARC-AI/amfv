AMFV Web
========

Fresh FastAPI full-stack app for AMFV eval-set creation and review.

Current baseline
----------------

- `backend/`: FastAPI, SQLModel, Alembic, SQLite.
- `frontend/`: Vite, React, TypeScript, TanStack Router/Query, Bun tooling.
- `docs/ingest-format.md`: AMFV ingest/export contract to preserve during the port.
- `docs/frontend-architecture.md`: React/API ownership, auth notes, and text-span contract.

Setup
-----

1. Copy `.env.example` to `.env` and set real secrets.
2. Install backend dependencies with `cd backend && uv sync`.
3. Apply migrations with `cd backend && uv run alembic upgrade head`.
4. Install frontend dependencies with `cd frontend && bun install`.

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

- Backend API: `cd backend && uv run fastapi dev app/main.py`
- Frontend: `cd frontend && bun run dev`

Useful checks
-------------

- `cd backend && uv run ruff check .`
- `cd backend && uv run pytest`
- `cd backend && uv run alembic upgrade head`
- `bash ./scripts/generate-client.sh`
- `cd frontend && bun run check-client`
- `cd frontend && bunx tsc -p tsconfig.build.json`
- `cd frontend && bunx biome check ./src ./tests`
- `cd frontend && bun run build`
- `bash ./scripts/run-frontend-e2e.sh`

Use targeted checks while developing a focused slice: run the checks for the
layer you touched plus `frontend`'s generated-client smoke check when API/client
contracts are involved. Use the full backend test suite and full Playwright
harness at integration points and before final handoff.

Current product shape
---------------------

The fresh app is React-first. End users enter through Review and Create, with Retrieval and Fact Decomposition under each. Admin/data users get dataset, document, moderation, task, export, and metrics APIs under `/api/v1/admin`. Basic user management still uses the template `/api/v1/users` surface.

The interactive product currently includes:

- source-document-backed retrieval and fact-decomposition creation;
- retrieval, relevance, and fact-decomposition review workflows;
- home/my-work summary counts and next-task recommendation;
- admin datasets, documents, moderation, task generation, export, user metrics, agreement, and inter-user agreement.

The AMFV-specific `/api/v1/admin/users*`, user-review-history, and ingest endpoints are still intentionally deferred and should not be presented as working surfaces.

Signup is invite-gated. OAuth is postponed. SQLite is the current database target, with Alembic migrations rather than `create_all`.

Development note
----------------

Bun is available in the current workflow. Frontend build, generated-client, and type checks are required for frontend/API slices. Run `bash ./scripts/generate-client.sh` after backend API contract changes and commit `frontend/openapi.json` plus regenerated `frontend/src/client/*`.
