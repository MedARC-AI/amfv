# AMFV Web Backend

## Requirements

* [uv](https://docs.astral.sh/uv/) for Python package and environment management.

Docker Compose files are inherited from the FastAPI template, but local backend development currently works directly from `backend/`.

## General Workflow

Install the full workspace from the monorepo root:

```bash
uv sync --dev
```

Apply migrations against SQLite:

```bash
uv run alembic upgrade head
```

Run the API:

```bash
uv run fastapi dev app/main.py
```

Models live in `app/models.py`, routes in `app/api/routes/`, and domain logic in `app/services/`.

## Backend tests

Run:

```bash
uv run ruff format --check web/backend
uv run ruff check web/backend
uv run ty check web/backend/app
uv run pytest web/backend/tests
```

Tests use a migrated template copied into a fresh SQLite database for every
test, with a separate temp directory per pytest process. Do not replace this
with a shared `data/app.db` fixture.

## Migrations

Alembic is configured for SQLite batch migrations. After changing models, create a migration from `backend/`:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

Do not switch back to `SQLModel.metadata.create_all()` for normal app startup.

## Auth

The app uses the FastAPI template password/JWT flow with invite-gated signup. AMFV roles and capability dependencies live in `app/api/deps.py`. Public signup without an invite is disabled.

## Documents and generated contracts

The backend consumes versioned source-document JSONL at the superuser-only
`/api/v1/admin/documents/import` endpoint. Producers and the neutral fixture live
under `datasets`; see `../docs/ingest-format.md`. Web does not own scraping.

After a public schema change, run `bash web/scripts/generate-client.sh` from the
monorepo root. Never edit `frontend/openapi.json` or `frontend/src/client`
manually.
