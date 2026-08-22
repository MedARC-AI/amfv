# AMFV Web Backend

## Requirements

* [uv](https://docs.astral.sh/uv/) for Python package and environment management.

Docker Compose files are inherited from the FastAPI template, but local backend development currently works directly from `backend/`.

## General Workflow

Install dependencies from `backend/`:

```bash
uv sync
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
uv run ruff check .
uv run pytest
```

## Migrations

Alembic is configured for SQLite batch migrations. After changing models, create a migration from `backend/`:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

Do not switch back to `SQLModel.metadata.create_all()` for normal app startup.

## Auth

The app uses the FastAPI template password/JWT flow with invite-gated signup. AMFV roles and capability dependencies live in `app/api/deps.py`. Public signup without an invite is disabled.
